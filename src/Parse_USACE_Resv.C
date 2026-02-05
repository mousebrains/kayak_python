#include <Parse_USACE_Resv.H>
#include <File.H>
#include <Tokenize.H>
#include <Convert.H>
#include <String.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace {
  const std::string ws(" \t\n");

  std::string mkName(std::string name) {
    String::trimInPlace(name, ws);

    for (std::string::size_type pos = 0, index; 
        (index = name.find_first_of(ws , pos)) != name.npos;) {
      const std::string::size_type i(name.find_first_not_of(ws, index));
      name.replace(index, i - index, "_");
      pos = index + 1;
    }
    return name; 
  }
}

namespace Parsers {
  USACE_Resv::USACE_Resv(const Curl& curl,
                         const bool qVerbose,
                         const bool qDryRun,
			 DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db),
      mState(0)
  { 
    serveUpLines(curl.str()); 
  }

  bool
  USACE_Resv::line(const std::string& l)
  {
    Tokenize tokens(l, "|+", true);

    if (mDebug)
      std::cout << mState << ' ' << tokens.size() << ' ' << l << std::endl;

    if (tokens.empty())
      return true;

    for (Tokenize::size_type i = 0; i < tokens.size(); ++i)
      tokens[i] = String::trim(tokens[i]);

    if (mState == 0) {
      if (l.find("U.S. ARMY ENGINEER DISTRICT, PORTLAND") != l.npos) {
        if (tokens.size() >= 2) {
          mTime = toDate(tokens[tokens.size() - 1]+ " 00:00:00", true);

          if (!mTime) {
            std::cerr << "Error converting (" << tokens[tokens.size() - 1] << ") into a date"
                      << std::endl;
            return false;
          }

          ++mState;
          mTime -= (12 * 60 * 60);
        }
      }
      return true;
    }

    if (mState == 1) {
      if ((tokens.size() >= 2) &&
          (tokens[tokens.size() - 1] == "OUTFLOW") && 
          (tokens[tokens.size() - 2] == "INFLOW")) 
        mState = 10;
      else if (tokens[tokens.size() - 1] == "DISCHARGE")
        mState = 20;
    }

    if (mState == 10) {
      if (!tokens.empty() && (tokens[tokens.size() - 1]  == "CFS"))
        ++mState;
      return true;
    }

    if (mState == 11) {
      if ((tokens.size() < 3) || 
          (tokens[0].find("TOTALS") != std::string::npos)) {
        mState = 1;
        return true;
      }
      const std::string project(mkName(tokens[0]));
      const double  inflow(toDouble(String::trim(tokens[tokens.size() - 2])));
      const double outflow(toDouble(String::trim(tokens[tokens.size() - 1])));
      if (finite(inflow)) 
        dumpToDatabase(project, DataDB::INFLOW, mTime, inflow);
      if (finite(outflow)) 
        dumpToDatabase(project, DataDB::FLOW, mTime, outflow);

      return true;
    }

    if (mState == 20) {
      mStacked = false;
      if (!tokens.empty() && (String::trim(tokens[tokens.size() - 1]) == "CFS"))
        ++mState;
      return true;
    }

    if (mState == 21) {
      if (tokens.size() < 3) {
        mState = 1;
        if (mStacked) {
          const std::string project(mkName(mProject));
          if (finite(mFlow))
            dumpToDatabase(project, DataDB::FLOW, mTime, mFlow); 
          if (finite(mGage))
            dumpToDatabase(project, DataDB::GAGE, mTime, mGage); 
          mStacked = false;
        }
        return true;
      }
      if (mStacked) {
        std::string project;
        if (tokens[0].find("-------") != std::string::npos) {
          project = mkName(mProject);
        } else {
          project = mkName(mProject + " " + String::trim(tokens[0]));
        }
        mStacked = false;
        if (finite(mFlow)) 
          dumpToDatabase(project, DataDB::FLOW, mTime, mFlow);
        if (finite(mGage)) 
          dumpToDatabase(project, DataDB::GAGE, mTime, mGage);
      }

      const double flow(toDouble(String::trim(tokens[tokens.size() - 1])));
      const double gage(toDouble(String::trim(tokens[tokens.size() - 3])));

      if(finite(flow) || finite(gage)) {
        mStacked = true;
        mProject = String::trim(tokens[0]);
        mFlow = flow;
        mGage = gage;
      }
      return true;
    }

    return true;

            // dumpToDatabase(station, DataDB::GAGE, mTime, value);
  }
}
