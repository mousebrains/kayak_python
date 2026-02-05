#include <Parse_NWRFC_XML.H>
#include <File.H>
#include <Tokenize.H>
#include <DataDB.H>
#include <String.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

Parsers::NWRFC_XML::NWRFC_XML(const Curl& curl,
                              const bool qVerbose,
                              const bool qDryRun,
		              DataDB& db)
  : Parse(curl.url(), qVerbose, qDryRun, db)
  , mState(0)
  , mqHeight(false)
  , mqFlow(false)
  , mHeight(0)
  , mFlow(0)
{
  serveUpLines(curl.str());
}

bool
Parsers::NWRFC_XML::line(const std::string& l)
{
  if (mDebug)
  std::cout << mState << " " << l << std::endl;

  if (mState == 0) { // Look for SiteDate field
    if (l.find("<SiteData id=") == 0) { // Must be first in line
      Tokenize tokens(l, "\"", true);
      if (tokens.size() == 3) {
        mStation = tokens[1];
        mState = 1;
      }
    }
    return true;
  }

  if (l.find("</observedData>") == 0) {
    mState = 1;
    return true;
  }

  if (mqWarn && !l.empty()) {
    mqWarn = false;
  }

  switch (mState) {
  case 1: // Look for <observedData>
    if (l.find("<observedData>") == 0) {
      ++mState;
    }
    break;
  case 2: // Look for <observedValue
    if (l.find("<observedValue ") == 0) { // Starting an observed value
      ++mState;
      mqHeight = false;
      mqFlow = false;
    }
    break;
  case 3: // Look for data content
    if (l.find("<dataDateTime>") == 0) { // date/time for observed value
      Tokenize tokens(l, "<> \t\n", true);
      if (tokens.size() != 3) {
        std::cerr << "Error converting '" << l << "' into a date/time" 
                  << std::endl;
        return false;
      }
      mTime = toDate(tokens[1], "GMT", true);
      mNow = time(0); // NWRFC has a bug during daylight savings times in Europe.
                      // Instead of using GMT, they are print London time, I believe. 
    } else if (l.find("<stage ") == 0) { // height
      if (l.find("units=\"feet\"") == l.npos) {
        std::cerr << "Unrecognized units in '" << l << "'" << std::endl;
        return false;
      }
      Tokenize tokens(l, "<>", true);
      if (tokens.size() != 3) {
        std::cerr << "Invalid record, '" << l << "'" << std::endl;
        return false;
      }
      mqHeight = true;
      mHeight = toDouble(tokens[1], true, true);
    } else if (l.find("<discharge ") == 0) { // flow
      if (l.find("units=\"cubic feet per second\"") == l.npos) {
        std::cerr << "Unrecognized units in '" << l << "'" << std::endl;
        return false;
      }
      Tokenize tokens(l, "<>", true);
      if (tokens.size() != 3) {
        std::cerr << "Invalid record, '" << l << "'" << std::endl;
        return false;
      }
      mqFlow = true;
      mFlow = toDouble(tokens[1], true, true);
    } else if (l.find("</observedValue>") == 0) { // End of observation
      mState = 2;
      if ((mTime <= mNow) && mqHeight && finite(mHeight)) {
        dumpToDatabase(mStation, DataDB::GAGE, mTime, mHeight);
      }
      if ((mTime <= mNow) && mqFlow && finite(mFlow)) {
        dumpToDatabase(mStation, DataDB::FLOW, mTime, mFlow);
      }
    }
    break;
  }

/*
<SiteData id="GIBO3">

<observedData>
<observedValue petype="HG" durCode="0" tsCode="RG" extremumCode="Z">
<dataDateTime>2010-09-24T04:00:00Z</dataDateTime>
<stage units="feet">2.74</stage>
<discharge units="cubic feet per second">51</discharge>
</observedValue>
*/

  return true;
}
