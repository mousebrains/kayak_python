#include <Parse_USACE_Outflow.H>
#include <Tokenize.H>
#include <String.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  USACE_Outflow::USACE_Outflow(const Curl& curl,
                               const bool qVerbose,
                               const bool qDryRun,
			       DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db), 
      mState(0)
  {
    serveUpLines(curl.str());
  } 
  
  bool
  USACE_Outflow::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << ' ' << l << std::endl;
 
    if (mState == 0) {
      const Tokenize tokens(l);
      Tokenize::const_iterator it(tokens.find("PROJECT-"));
      if (it != tokens.end()) {
        if (++it != tokens.end()) {
          ++mState;
          mProject = *it;
        }
      }
      return true;
    }

    if (mState == 1) {
      const std::string key("REPORT");
      std::string::size_type i(l.find(key));
      if (i == l.npos)
        mState = 0;
      else {
        mDate = String::collapse(String::trim(l.substr(i + key.size())));
        ++mState;
      }
      return true;
    }

    if (mState == 2) {
      const Tokenize tokens(l);
      if (tokens.size() >= 3) {
        const double value(toDouble(tokens[2]));
        if (finite(value)) {
          if (toInt(tokens[0])) {
            const std::string date(mDate + " " + tokens[0] + ":00");
            const time_t when(toDate(date, true));
            if (!when) 
              std::cerr << "Error converting '" << date << "' to a date" << std::endl;
            else 
  	      dumpToDatabase(mProject, DataDB::FLOW, when, value * 1000);
          }
        }
      }
      return true;
    }
 
    return true;
  }
}
