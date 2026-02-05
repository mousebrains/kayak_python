#include <Parse_OCS.H>
#include <File.H>
#include <Tokenize.H>
#include <Convert.H>
#include <String.H>
#include <Curl.H>
#include <iostream>

namespace {
  std::set<std::string> splits;
} // Anonymous

namespace Parsers {
  OCS::OCS(const Curl& curl,
           const bool qVerbose,
           const bool qDryRun,
	   DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db),
      mState(0),
      mFlow(0),
      mStage(0),
      mChange(0)
  {
    if (splits.empty()) {
      splits.insert("AT");
      splits.insert("NEAR");
      splits.insert("ABOVE");
    }

    serveUpLines(curl.str()); 
  }

  bool
  OCS::line(const std::string& l)
  {
    if (mDebug)
      std::cout << mState << " " << l << std::endl;

    switch (mState) {
    case 0:
      {
        Tokenize tokens(l);
	Tokenize::const_iterator it(tokens.find("NATIONAL"));
	if ((it != tokens.end()) && 
	    ((++it != tokens.end()) && (*it == "WEATHER")) &&
	    ((++it != tokens.end()) && (*it == "SERVICE")))
	  ++mState;
      }
      break;
    case 1:
      {
        if (l.size() > 4 && l[3] == ' ') {
          if ((mTime = toDate("0" + l, true)) == (time_t) -1)
            mState = 0;
          else
            ++mState;
        } else if ((mTime = toDate("0" + l, true)) == (time_t) -1)
          mState = 0;
        else
          ++mState;
      }
      break;
    case 2:
      if (l.substr(0, 5) == "RIVER") {
        ++mState;

	const std::string::size_type flow(l.find("FLOW"));
	const std::string::size_type stage(l.find("STAGE"));
	const std::string::size_type change(l.find("24 HR"));

	if (flow == l.npos) {
          mFlow = 0;
	} else {
	  mFlow = 1;
	  if ((stage != l.npos) && (stage > flow))
	    mFlow += 2;
	  if ((change != l.npos) && (change > flow))
	    mFlow += 2;
	}

	if (stage == l.npos) {
          mStage = 0;
	} else {
	  mStage = 1;
	  if ((flow != l.npos) && (flow > stage))
	    mStage += 2;
	  if ((change != l.npos) && (change > stage))
	    mStage += 2;
	}
		  
	if (change == l.npos) {
          mChange = 0;
	} else {
	  mChange = 1;
	  if ((flow != l.npos) && (flow > change))
	    mChange += 2;
	  if ((stage != l.npos) && (stage > change))
	    mChange += 2;
	}

	mEnd = (mFlow > mStage) ? 
	       ((mFlow > mChange) ? mFlow : mChange) :
	       (mStage > mChange ? mStage : mChange);
      }
      break;
    case 3:
      {
        if (l.find("RANGE OF THE PORTLAND HARBOR") != l.npos) {
          mState = 0;
          break;
        }
        const Tokenize tokens(l, " \t\n", true);
        const Tokenize::size_type len(tokens.size());
        bool qFound(true);

        if ((mFlow > 0) && ((mFlow >= len) || 
			    ((tokens[len-mFlow] != "CFS") && 
			     (tokens[len-mFlow] != "KCFS") && 
			     (tokens[len-mFlow] != "AVBL"))))
	  qFound = false;

        if ((mStage > 0) && ((mStage >= len) || 
			     ((tokens[len-mStage] != "FT") &&
			     ((tokens[len-mStage] != "AVBL")))))
	  qFound = false;

        if ((mChange > 0) && ((mChange >= len) || 
			      ((tokens[len-mChange] != "FT") &&
			       (tokens[len-mChange] != "AVBL"))))
	  qFound = false;

        if (!qFound) {
          mName.clear();
	  for (Tokenize::size_type i = 0; i < len; ++i) {
            if (!mName.empty())
              mName += "_";
	    mName += tokens[i];
	  }
	  break;
        }

	if (splits.find(tokens[0]) == splits.end())
	  mName.clear();

        std::string station(mName);

        for (Tokenize::size_type i(0), e(len - mEnd - 2); (i <= e) && (i < len); ++i) {
          if (!station.empty())
            station += "_";
	  station += tokens[i];
        }

	if (mName.empty()) {
          for (Tokenize::size_type i(0), e(len - mEnd - 2); (i <= e) && (i < len); ++i) {
	    if (splits.find(tokens[i]) != splits.end())
              break;
	    if (!mName.empty())
              mName += "_";
	    mName += tokens[i];
	  }
	}

        if (mFlow > 0) {
	  const std::string flow(String::trim(tokens[len - mFlow - 1]));
          std::istringstream iss(flow);
	  double value;
          if ((iss >> value) && ((size_t) iss.tellg() == flow.size()) && (value > 0))  {
            if (tokens[len-mFlow] == "KCFS")
	      value *= 1000;
            dumpToDatabase(station, DataDB::FLOW, mTime, value);
	  }
        }

        if (mStage > 0) {
	  const std::string stage(String::trim(tokens[len - mStage - 1]));
          std::istringstream iss(stage);
	  double value;
          if ((iss >> value) && ((size_t) iss.tellg() == stage.size())) {
            dumpToDatabase(station, DataDB::GAGE, mTime, value);
	  }
        }
      }
      break;
    default:
      std::cerr << "Unrecognized state " << mState << " " << mURL << std::endl;
      return false;
    }
    return true;
  }
}
