#include <Parse_USBR_PN.H>
#include <File.H>
#include <String.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  USBR_PN::USBR_PN(const Curl& curl,
                   const bool qVerbose,
                   const bool qDryRun,
		   DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db),
      mState(0)
  {
    serveUpCookedLines(curl.str()); 
  }

  bool
  USBR_PN::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << ' ' << l << std::endl;

    Tokenize tokens(l, "|", false);

    if (l.empty()) {
      mState = 0;
      return true;
    }

    if (tokens.size() < 2)
      return true;

    if (mState == 0) {
      if (String::trim(tokens[0]).empty()) {
        ++mState;
        mStations.clear();
        for (Tokenize::size_type i = 1; i < tokens.size(); ++i) {
          const std::string station(String::trim(tokens[i]));
          if (!station.empty()) 
            mStations.insert(std::make_pair(i, station));
        }
      }
      return true;
    }

    if (mState == 1) {
      if (String::trim(tokens[0]).empty()) {
        ++mState;
        mTypes.clear();
        for (Tokenize::size_type i = 1; i < tokens.size(); ++i) {
          const std::string type(String::trim(tokens[i]));
          if (!type.empty()) {
            if (type == "GH" || type == "CH") {
              mTypes.insert(std::make_pair(i, DataDB::GAGE));
            } else if (type == "Q" || type == "QC") {
              mTypes.insert(std::make_pair(i, DataDB::FLOW));
            } else if (type == "WF") {
              mTypes.insert(std::make_pair(i, DataDB::TEMPERATURE));
            } else if (type != "AF" && 
                       type != "FB" && 
                       type != "PC" && 
                       type != "FB" && 
                       type != "OB")
              std::cerr << "Unrecognized field " << type << std::endl;
          } 
        }
      }
      return true;
    }

    const time_t time(toDate(String::trim(tokens[0])));

    if (!time) 
      throw "Error converting '" + String::trim(tokens[0]) + "' into a date";

    for (tStations::const_iterator it = mStations.begin(); it != mStations.end(); ++it) {
      Tokenize::size_type index(it->first);
      if (index >= tokens.size()) 
        continue;
      tTypes::const_iterator tt(mTypes.find(index));
      if (tt == mTypes.end())
        continue;

      const std::string str(String::trim(tokens[index]));
      if (!str.empty()) {
        const std::string& station(it->second);
          const DataDB::TYPE& type(tt->second);
          const double value(toDouble(str, false, false));
          if (finite(value))
            dumpToDatabase(station, type, time, value);
      }
    }
    
    return true;
  }
}
