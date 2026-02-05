#include <Parse_USACE_Ca.H>
#include <File.H>
#include <Convert.H>
#include <Tokenize.H>
#include <String.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  USACE_Ca::USACE_Ca(const Curl& curl,
                     const bool qVerbose,
                     const bool qDryRun,
		     DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db), 
      mState(0)
  { 
    const std::string::size_type n(mURL.rfind('?'));
    if (n != mURL.npos)
      mNamePrefix = mURL.substr(n + 1) + " ";

    serveUpLines(curl.str()); 
  }

  bool
  USACE_Ca::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << " " << l << std::endl;
  
    if (String::trim(l).empty()) {
      mState = 0;
      mLines.clear();
      return true;
    }

    Tokenize tokens(l);

    if (mState == 0) {
      if (!tokens.empty() && (tokens[0] == "Date")) {
        parseHeader(l, tokens);
        ++mState;
        mLines.clear();
      } else
        mLines.push_back(l);
      return true;
    }

    if (tokens.empty()) {
      mState = 0;
      return true;
    }

    if (tokens.size() < 2)
      return true;

    std::string time(tokens[1]);
    if (time == "2400")
      time = "2359";
 
    const std::string date(tokens[0] + " " + time);
    const time_t when(toDate(date, true));

    if (!when) {
      std::cerr << "Error converting '" << date << "' to a date." << std::endl;
      return true;
    }

    for (tNames::size_type i = 0; i < mNames.size(); ++i) {
      const std::string& name(mNames[i]);
      const DataDB::TYPE& type(mTypes[i]);
      const std::string::size_type start(mIndices[i].first);
      const std::string::size_type len(mIndices[i].second);

      if (l.size() > start) {
        const std::string& str(String::trim(l.substr(start, len)));
        if (!str.empty()) {
          const double value(toDouble(str, false));
          if (finite(value)) 
            dumpToDatabase(name, type, when, value);
        }
      }
    }

    return true;
  }

  void 
  USACE_Ca::parseHeader(const std::string& l,
                        const Tokenize& tokens)
  {
    std::string::size_type offset(l.npos);

    for (Tokenize::size_type i = tokens.size() - 1; i >= 2; --i) {
      if (((tokens[i] == "Flow") && (tokens[i-1] == "Stage")) ||
          ((tokens[i] == "Flow") && (tokens[i-1] == "Stg"))) {
        std::string::size_type indexStage(l.rfind(tokens[i-1], offset));
        std::string::size_type indexFlow(l.rfind(tokens[i], offset));
        offset = handleInOutFlow(indexFlow, offset, l);

        const std::string name(extractName(indexStage - 1, offset));
        if (!name.empty()) {
          mNames.push_back(name); 
          mIndices.push_back(std::make_pair(indexFlow, 
                                            offset == l.npos ? l.npos : offset - indexFlow + 1));
          mTypes.push_back(DataDB::FLOW);

          mNames.push_back(name); 
          mIndices.push_back(std::make_pair(indexStage - 1, indexFlow - indexStage + 1));
          mTypes.push_back(DataDB::GAGE);
        }
        --i;
        offset = indexStage - 1;
      } else if ((tokens[i] == "Outflow") || 
                 (tokens[i] == "Inflow") || 
                 (tokens[i] == "Flow")) {
        const std::string::size_type index(l.rfind(tokens[i], offset));
        offset = handleInOutFlow(index, offset, l);
        const std::string name(extractName(index-1, offset));
        if (!name.empty()) {
          mNames.push_back(name);
          mIndices.push_back(std::make_pair(index - 1, 
                                            offset == l.npos ? l.npos : offset - index + 1));
          mTypes.push_back(DataDB::type(tokens[i]));
        }
        offset = index - 1;
      } else if (tokens[i] == "Kernville") {
        std::string::size_type index(l.rfind(tokens[i], offset));
        offset = handleInOutFlow(index, offset, l);
        const std::string name(extractName(index-1, offset));
        if (!name.empty()) {
          const std::string::size_type stageLength(5);

          mNames.push_back(name); 
          mIndices.push_back(std::make_pair(index + stageLength, 
                                            offset == l.npos ? l.npos : 
                                            offset - (index + stageLength) + 1));
          mTypes.push_back(DataDB::FLOW);

          mNames.push_back(name); 
          mIndices.push_back(std::make_pair(index, stageLength));
          mTypes.push_back(DataDB::GAGE);
        }
        offset = index - 1;
      } else if ((i > 3) &&
                 (tokens[i] == "Out") && 
                 (tokens[i-1] == "Fish") &&
                 (tokens[i-2] == "Flow") &&
                 (tokens[i-3] == "Stg")) {
        std::string::size_type indexStage(l.rfind(tokens[i-3], offset));
        std::string::size_type indexFlow(l.rfind(tokens[i], offset));
        offset = handleInOutFlow(indexFlow, offset, l);
        const std::string name(extractName(indexStage - 1, offset));
        if (!name.empty()) {
          mNames.push_back(name); 
          mIndices.push_back(std::make_pair(indexFlow, 
                                            offset == l.npos ? l.npos : offset - indexFlow + 1));
          mTypes.push_back(DataDB::FLOW);

          mNames.push_back(name); 
          mIndices.push_back(std::make_pair(indexStage - 1, 5));
          mTypes.push_back(DataDB::GAGE);
        }
        i -= 3;
        offset = indexStage - 1;
      }
    }
  }

  std::string
  USACE_Ca::extractName(const std::string::size_type start,
                        const std::string::size_type stop)
  {
    const std::string delim(" -@.\t\n");
    std::string name;

    for (tLines::size_type i = (mLines.size() > 3) ? (mLines.size() - 3) : 0;
         i < mLines.size(); ++i) {
      const std::string& line(mLines[i]);
      if (line.size() <= start)
        continue;
      const std::string::size_type sDash(line.find("---"));
      const std::string::size_type eDash(line.rfind("---"));

      std::string substr;

      if ((sDash == line.npos) || (eDash < start) || (sDash > stop))
        substr = line.substr(start, stop == std::string::npos ? stop : (stop - start));
      else 
        substr = line.substr(sDash, eDash - sDash);

      substr = String::trim(substr, delim);
      if ((substr != "Computed") && 
          (substr != "Outflow") &&
          (substr != "Inflow") &&
          (substr != "Total"))
        name += " " + substr;
    }

    String::replaceInPlace(name, "*", std::string());

    return String::collapse(String::trim(name, delim), "_", delim);
  }

  std::string::size_type
  USACE_Ca::handleInOutFlow(const std::string::size_type index,
                            std::string::size_type offset,
                            const std::string& l)
  {
    if (mLines.empty())
      return offset;

    const std::string& prevLine(mLines[mLines.size() - 1]);
    const std::string::size_type  inflow(prevLine.rfind("Inflow", offset));
    const std::string::size_type outflow(prevLine.rfind("Outflow", offset));

    if ((inflow == prevLine.npos) && (outflow == prevLine.npos))
      return offset;

    if (inflow == prevLine.npos) { // only an outflow
      if (outflow <= index)
        return offset;
      const std::string name(extractName(outflow, offset));
      if (name.empty())
        return offset;
      mNames.push_back(name);
      mTypes.push_back(DataDB::FLOW);
      mIndices.push_back(std::make_pair(outflow, offset == std::string::npos ? std::string::npos :
                                                 offset - outflow + 2));
      return outflow - 1;
    } else if (outflow == prevLine.npos) { // only an inflow
      if (inflow <= index)
        return offset;
      const std::string name(extractName(inflow, offset));
      if (name.empty())
        return offset;
      mNames.push_back(name);
      mTypes.push_back(DataDB::INFLOW);
      mIndices.push_back(std::make_pair(inflow, offset == std::string::npos ? std::string::npos :
                                                 offset - inflow + 2));
      return inflow - 1;
    } else if (inflow < outflow) {
      if (outflow > index) { // Do outflow first
        const std::string name(extractName(outflow, offset));
        if (!name.empty()) {
          mNames.push_back(name);
          mTypes.push_back(DataDB::FLOW);
          mIndices.push_back(std::make_pair(outflow, 
                                            offset == std::string::npos ? std::string::npos :
                                            offset - outflow + 2));
          offset = outflow - 1;
        }
      }
      if (inflow > index) { // Do inflow second
        const std::string name(extractName(inflow, offset));
        if (!name.empty()) {
          mNames.push_back(name);
          mTypes.push_back(DataDB::INFLOW);
          mIndices.push_back(std::make_pair(inflow, 
                                            offset == std::string::npos ? std::string::npos :
                                            offset - inflow + 2));
          offset = inflow - 1;
        }
      }
      return offset;
    } else {
      if (inflow > index) { // Do inflow first
        const std::string name(extractName(inflow, offset));
        if (!name.empty()) {
          mNames.push_back(name);
          mTypes.push_back(DataDB::INFLOW);
          mIndices.push_back(std::make_pair(inflow, 
                                            offset == std::string::npos ? std::string::npos :
                                            offset - inflow + 2));
          offset = inflow - 1;
        }
      }
      if (outflow > index) { // Do outflow second
        const std::string name(extractName(outflow, offset));
        if (!name.empty()) {
          mNames.push_back(name);
          mTypes.push_back(DataDB::FLOW);
          mIndices.push_back(std::make_pair(outflow, 
                                            offset == std::string::npos ? std::string::npos :
                                            offset - outflow + 2));
          offset = outflow - 1;
        }
      }
      return offset;
    }

    return offset;
  }
}
