#include <Parse_IDWR.H>
#include <File.H>
#include <String.H>
#include <DataDB.H>
#include <Tokenize.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  IDWR::IDWR(const Curl& curl,
             const bool qVerbose,
             const bool qDryRun,
	     DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db),
      mState(0)
  {
    serveUpLines(curl.str()); 
  }

  bool
  IDWR::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << ' ' << l << std::endl;

    if (l.empty()) {
      mState = 0;
      return true;
    }

    Tokenize tokens(l);

    if (tokens.size() < 3) {
      mState = 0;
      return true;
    }

    if (mState == 0) {
      if ((tokens[0] == "WY") && (tokens[1] == "Station") && (tokens[2] == "Parameter")) {
        ++mState;
        mTimes.clear();
        for (Tokenize::size_type i = 4; i < tokens.size(); i += 2) // Two fields/date
          mTimes.push_back(tokens[i - 1] + " " + tokens[i]);
      }
      return true;
    }

    if (mState == 1) { // Get column widts by looking for blanks between equal signs
      mColumns.clear();
      for (std::string::size_type count = 0, pos = 0, index; 
           (index = l.find_first_not_of("=", pos)) != l.npos; pos = index + 1, ++count) 
        mColumns.push_back(index);
      mState = (mColumns.size() > 3) ? (mState + 1) : 0;
      return true;
    }

    const time_t now(time(0));
    const std::string& waterYear(tokens[0]);
    const std::string prevYear(Convert::toStr(Convert::strTo<int>(waterYear) - 1));
    const std::string& station(tokens[1]);
    const std::string& typeStr(tokens[2]);
    const tColumns::size_type timesOffset(3);
    DataDB::TYPE type;

    if (typeStr == "GD" || typeStr == "GH" || typeStr == "GH/Q") 
      type = DataDB::GAGE;
    else if (typeStr == "Q" || typeStr == "QD" || typeStr == "QT")
      type = DataDB::FLOW;
    else {
      std::cerr << "Unrecognized information type(" << typeStr << ")" << std::endl;
      return true;
    }
     
    for (tColumns::size_type i = timesOffset; i < mColumns.size(); ++i) {
      const tColumns::size_type index(i - timesOffset);

      if (index >= mTimes.size()) {
        std::cerr << "Column size mismatch, expected at most " << mTimes.size()
                  << " data fields, but found " << (mColumns.size() - timesOffset)
                  << std::endl;
        mState = 0;
        return true;
      }
      const std::string field(getField(l, i));
      if (field.empty() || (field.find(' ') != field.npos))
        continue; // Skip if empty or embedded spaces

      const double value(toDouble(field));
      if (finite(value)) {
        time_t when(toDate(waterYear + " " + mTimes[index] + " 12:00", true));
        if (!when)
          continue;
        if (when > now) { // because of water year issues, if in the futures, back up a year
          when = toDate(waterYear + " " + mTimes[index] + " 12:00", true);
          if (!when)
            continue;
        }
        dumpToDatabase(station, type, when, value);
      }
    }

    return true;
  }

  std::string
  IDWR::getField(const std::string& l,
                 const tColumns::size_type i) const
  {
    if (mColumns.empty() || l.empty())
      return std::string();

    tColumns::size_type start(0);
    tColumns::size_type next(l.npos);

    if (i < mColumns.size()) {
      start = i ? (mColumns[i - 1] + 1) : 0;
      next = mColumns[i];
    } else 
      start = mColumns[mColumns.size() - 1] + 1;

    if (start < l.size())
      return String::trim(l.substr(start, next - start + 1));

    return std::string();
  }
}
