#include <Parse_NWRFC.H>
#include <File.H>
#include <Convert.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  NWRFC::NWRFC(const Curl& curl,
               const bool qVerbose,
               const bool qDryRun,
	       DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db), 
      mStation(initStation(mURL)),
      mState(0),
      mColGage(-1),
      mColFlow(-1),
      mColDate(0),
      mColTime(1),
      mMinCols(2),
      mqInflow(false)
  { 
    serveUpLines(curl.str()); 
  }

  std::string
  NWRFC::initStation(const std::string& url) const
  {
    Tokenize tokens(File::tail(url), ".");

    if (tokens.size())
      return tokens[0];

    return "";
  }

  bool
  NWRFC::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << " " << l << std::endl;

    if (mState == 0) {
      if (l.find("Inflow") != l.npos)
        mqInflow = true;

      if ((l.find("(ft)") != l.npos) || (l.find("(cfs)") != l.npos)) {
	++mState;
	Tokenize tokens(l, " \t\n", true);
	for (Tokenize::size_type i = 0; i < tokens.size(); ++i) {
	  if ((mColGage < 0) && (tokens[i] == "(ft)"))
	    mColGage = i + 1;
	  else if ((mColFlow < 0) && (tokens[i] == "(cfs)"))
	    mColFlow = i + 1;
	}
      }
      return true;
    }
    
    Tokenize tokens(l, " \t\n", true);

    if (tokens.size() >= mMinCols) {
      const std::string date(tokens[mColDate] + " " + tokens[mColTime]);
      const time_t when(toDate(date));
      const time_t now(time(0));
      if ((when != -1) && (when < now)) {
	if (mColGage != -1) {
	  const double gage(toDouble(tokens[mColGage]));
	  if (finite(gage))
	    dumpToDatabase(mStation, DataDB::GAGE, when, gage);
	}
	if (mColFlow != -1) {
	  const double flow(toDouble(tokens[mColFlow]));
	  if (finite(flow)) {
            if (flow > 0) {
	      dumpToDatabase(mStation, mqInflow ? DataDB::INFLOW : DataDB::FLOW, when, flow);
	    } else {
	      std::cerr << mStation << " has a negative flow(" << flow
		        << ") on " << ctime(&when) << " qIn " << mqInflow
			<< std::endl
			<< mURL
			<< std::endl
			<< l
			<< std::endl
			<< mText
			<< std::endl;
            }
	  }
	}
      }
    }
    
    return true;
  }
}
