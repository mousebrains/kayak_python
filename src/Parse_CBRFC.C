#include <Parse_CBRFC.H>
#include <Convert.H>
#include <Curl.H>
#include <TimeZone.H>
#include <String.H>
#include <iostream>
#include <cmath>

namespace Parsers {

  CBRFC::CBRFC(const Curl& curl,
               const bool qVerbose,
               const bool qDryRun,
	       DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db),
      mState(0)
  {
    TimeZone tz("GMT");
    serveUpCookedLines(curl.str());
  }

  bool
  CBRFC::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << " " << l << std::endl;    

    switch (mState) {
    case 0:
      { // The current time is: 09/26.04:37 UTC
        const Tokenize tokens(l);
        if ((tokens.size() == 6) &&
            (tokens[0] == "The") &&
            (tokens[1] == "current") &&
            (tokens[2] == "time") &&
            (tokens[3] == "is:") &&
            ((tokens[5].find("GMT") == 0) || (tokens[5].find("UTC") == 0))) {
          if (!Convert::toTime(tokens[4], mTM)) {
            std::cerr << "Error converting '" << tokens[4] << "' to a time" << std::endl;
            return false;
          }
	  ++mState;
        }
      }
      break;
    case 1:
      {
	const Tokenize tokens(l, "|", false);
	if ((tokens.size() > 1) && (tokens[0] == "#")) {
	  for (Tokenize::size_type i = 1; i < tokens.size(); ++i)
	    mColumns.insert(std::make_pair(tokens[i], i));
	  static const char *required[] = {"Date", "ID", "Flow", "Stage", 0};
	  bool okay(true);
	  for (int i = 0; required[i]; ++i)
	    if (mColumns.find(required[i]) == mColumns.end()) {
	      std::cerr << "ERROR: required column, " 
			<< required[i] << ", not found for " << mURL << std::endl;
	      okay = false;
	    }
	  if (okay)
	    ++mState;
	}
      }
      break;
    case 2:
      {
	const Tokenize tokens(l, "|", false);
	time_t when(getTime(tokens));
	const time_t now(time(0));

	if (when > now) {
          when -= 3600; // Back up one hour, since sometimes it gets ahead of itself
	  if (when > now) // Too far ahead
	    when = -1;
	}

	if (when != -1) {
	  const std::string& id(getString(tokens, "ID"));
	  {
	    const double flow(getDouble(tokens, "Flow"));
	    if (finite(flow) && (flow > 0))
	      dumpToDatabase(id, "flow", when, flow);
	  }
	  {
	    const double gage(getDouble(tokens, "Stage"));
	    if (finite(gage)) 
	      dumpToDatabase(id, "gauge", when, gage);
	  }
	}
      }
      break;
    default:
      std::cerr << "Unsupported state(" << mState << ") for url=" << mURL << std::endl;
    }

    return true;
  }

  time_t
  CBRFC::getTime(const Tokenize& tokens) const
  {
    const double ddhh(getDouble(tokens, "Date"));
    if (std::isinf(ddhh))
      return -1;

    const int dd((int) floor(ddhh));
    const int hh((int) floor((ddhh - (double) dd) * 100 + 0.5));

    tm tm;
    memcpy(&tm, &mTM, sizeof(tm));

    tm.tm_sec = 0;
    tm.tm_min = 0;
    tm.tm_hour = hh;
    tm.tm_mday = dd;

    const time_t when(mktime(&tm)); // + tm.tm_gmtoff);

    return when;
  }

  double
  CBRFC::getDouble(const Tokenize& tokens,
		  const std::string& field) const
  {
    tColumns::const_iterator it(mColumns.find(field));

    if ((it == mColumns.end()) || (it->second >= tokens.size()))
      return toDouble("INFINITY");

    return toDouble(tokens[it->second]);
  }

  const std::string&
  CBRFC::getString(const Tokenize& tokens,
		   const std::string& field) const
  {
    tColumns::const_iterator it(mColumns.find(field));

    if ((it == mColumns.end()) || (it->second >= tokens.size())) {
      static std::string empty;
      return empty;
    }

    return tokens[it->second];
  }
}
