#include <Parse_NOAA.H>
#include <File.H>
#include <Convert.H>
#include <Tokenize.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  NOAA::NOAA(const Curl& curl,
             const bool qVerbose,
             const bool qDryRun,
	     DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db), 
      mState(0),
      mStation(initStation(mURL))
  { 
    const time_t now(time(0));
    localtime_r(&now, &mTM);

    serveUpLines(curl.str()); 
  }

  void
  NOAA::getValue(const time_t& when,
		 const std::string& field)
  {
    std::string::size_type offset;

    if ((offset = field.find("CFS")) != field.npos) {
      const double value(toDouble(field.substr(0, offset)));
      if (finite(value))
	dumpToDatabase(mStation, DataDB::FLOW, when, value);
    } else if ((offset = field.find("Ft")) != field.npos) {
      const double value(toDouble(field.substr(0, offset)));
      if (finite(value))
	dumpToDatabase(mStation, DataDB::GAGE, when, value);
    }
  }

  std::string
  NOAA::initStation(const std::string& url) const
  {
    return File::tail(File::rootname(url)).substr(0,4);
  }

  bool
  NOAA::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << " " << l << std::endl;

    switch (mState) {
    case 0: 
      {
        Tokenize tokens(l);

        if((tokens.size() >= 2) && (tokens[0] == "Observed") && (tokens[1] == "Data:"))
	  ++mState;
      }
      break;
    case 1:
      {
        const std::string key1("Flow");
        const std::string key2("Stage");
        const std::string::size_type i1(l.find(key1));
        const std::string::size_type i2(l.find(key2));
        const std::string::size_type i((i1 == l.npos) ? i2 : 
                                       (i2 == l.npos) ? i1 :
                                       (i1 > i2) ? i1 : i2);
        const std::string::size_type j(l.find("|", i));
        if (j != l.npos) {
          mColIndex = j + 1;
          ++mState; // Skip line
        }
      }
      break;
    case 2:
      if (!l.empty()) {
        Tokenize tokens(l.substr(0, mColIndex));
        if (tokens.size() >= 3) {
	  struct tm tm;
	  if (Convert::toTime(tokens[0] + " " + tokens[1], tm)) {
	    if (tm.tm_mon > (mTM.tm_mon + 1))
	      --tm.tm_year;
	    const time_t when(mktime(&tm));

	    getValue(when, tokens[2]);
	    if (tokens.size() >= 4)
	      getValue(when, tokens[3]);
	  }
	}
      }
      break;
    default:
      std::cerr << "Unsupported state(" << mState << ") for url=" << mURL << std::endl;
    }

    return true;
  }
}
