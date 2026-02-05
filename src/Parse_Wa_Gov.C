#include <Parse_Wa_Gov.H>
#include <File.H>
#include <String.H>
#include <DataDB.H>
#include <Tokenize.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

Parsers::Wa_Gov::Wa_Gov(const Curl& curl,
                        const bool qVerbose,
                        const bool qDryRun,
		        DataDB& db)
  : Parse(curl.url(), qVerbose, qDryRun, db)
  , mState(0)
  , mType(DataDB::FLOW)
{
  serveUpLines(curl.str()); 
}

bool
Parsers::Wa_Gov::line(const std::string& l)
{
  if (mDebug)
    std::cout << mState << ' ' << l << std::endl;

  if (l.empty()) 
    return true;

  Tokenize tokens(l, " \t\n", true);

  if (tokens.size() < 2)
    return true;

  if (mState == 0) { // Looking for Site and Year
    if ((tokens.size() > 1) && (tokens[0] == "DATE") && (tokens[1] == "TIME")) {
      ++mState;
      mType = DataDB::FLOW;
      if (tokens.size() >= 3) {
        if (tokens[2].find("Water") == 0) {
          mType = DataDB::TEMPERATURE;
        } else if (tokens[2].find("Stage") == 0) {
          mType = DataDB::GAGE;
        }
      }
    } else {
      std::string::size_type index(tokens[0].find("--"));
      if (index != tokens[0].npos) {
        mStation = tokens[0].substr(0, index);
      }
    }
    return true;
  }
      
  if (mState == 1) {
    if (tokens[0].find("---") == 0) {
      ++mState;
    }
    return true;
  }

  if (tokens[0] == "Quality") {
    mState = 0;
    return true;
  }

  if (mStation.empty() || (tokens.size() <= 3))
    return true;

  if (l.find("No Data") != l.npos) {
    return true;
  }

  const double quality(toDouble(tokens[tokens.size() - 1], true, true));
  if ((quality > 0) && (quality < 200)) { // Data may have some validty
    const std::string date(tokens[0] + " " + tokens[1]);
    const time_t time(toDate(date, true));
    double value(toDouble(tokens[2], true, true));
    if ((time > 0) && finite(value)) {
      if (mType == DataDB::TEMPERATURE) {
          value = value * 1.8 + 32; // C -> F
      }
      dumpToDatabase(mStation, mType, time, value);
    }
  }

  return true;
}
