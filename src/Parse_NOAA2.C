#include <Parse_NOAA2.H>
#include <File.H>
#include <Convert.H>
#include <Tokenize.H>
#include <Curl.H>
#include <String.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  NOAA2::NOAA2(const Curl& curl,
             const bool qVerbose,
             const bool qDryRun,
	     DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db), 
      mState(0),
      mStation(initStation(mURL))
  {
    if (mStation.empty()) 
      throw std::logic_error("Unable to make a station name out of (" + mURL + ")");
 
    const time_t now(time(0));
    localtime_r(&now, &mTM);

    serveUpCookedLines(curl.str()); 
  }

  void
  NOAA2::getValue(const time_t& when,
		 const std::string& field)
  {
    const std::string f(String::toUpper(field));
    std::string::size_type offset;

    if ((offset = f.find("KCFS")) != f.npos) {
      const double value(toDouble(f.substr(0, offset)));
      if (finite(value))
	dumpToDatabase(mStation, DataDB::FLOW, when, value * 1000);
    } else if ((offset = f.find("CFS")) != f.npos) {
      const double value(toDouble(f.substr(0, offset)));
      if (finite(value))
	dumpToDatabase(mStation, DataDB::GAGE, when, value);
    } else if ((offset = f.find("FT")) != f.npos) {
      const double value(toDouble(f.substr(0, offset)));
      if (finite(value))
	dumpToDatabase(mStation, DataDB::GAGE, when, value);
    }
  }

  std::string
  NOAA2::initStation(const std::string& url) const
  {
    std::string::size_type i(url.rfind('='));

    return (i == url.npos) ? std::string() : url.substr(i + 1);
  }

  bool
  NOAA2::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << " " << l << std::endl;

    if (mState == 0) {
      if (l.find("| Observed Data:") != l.npos)
        ++mState;
      return true;
    }

    if (mState == 1) {
      const std::string key("Date(");
      const std::string::size_type index(l.find(key));
      if ((index != l.npos) && ((index + key.size() + 4) < l.size())) {
        ++mState; 
        mTimeZone(l.substr(index + key.size(), 3));
      }
      return true;
    }

    // if (l.find("| Forecast Data:") != l.npos) {
      // mState = 0;
      // return true;
    // }

    const time_t now(time(0));

    Tokenize tokens(l, " |\t\n");

    if (tokens.size() >= 3) {
      struct tm tm;
      if (Convert::toTime(tokens[0] + " " + tokens[1], tm)) {
	if (tm.tm_mon > (mTM.tm_mon + 1))
	  --tm.tm_year;
	const time_t when(mktime(&tm));
        if (when < now) {
          for (Tokenize::size_type i = 2; i < tokens.size(); ++i) {
            if (!tokens[i].empty()) {
              getValue(when, tokens[i]);
            }
          }
        }
      }
    }

    return true;
  }
}
