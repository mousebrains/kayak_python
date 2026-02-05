#include <Parse_USBR_Special.H>
#include <File.H>
#include <Tokenize.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  USBR_Special::USBR_Special(const Curl& curl,
                             const bool qVerbose,
                             const bool qDryRun,
			     DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db)
  {
    mZones.insert(std::make_pair("P", 0));
    mZones.insert(std::make_pair("M", -3600));
    mZones.insert(std::make_pair("C", -3600 * 2));
    mZones.insert(std::make_pair("E", -3600 * 3));

    mTypes.insert(std::make_pair("QRIRG", "flow"));
    mTypes.insert(std::make_pair("HGIRG", "gage"));
    
    serveUpLines(curl.str()); 
  }

  bool
  USBR_Special::line(const std::string& l)
  {
    Tokenize tokens(l);

    if (mDebug)
      std::cout << l << std::endl;

    if (tokens.size() < 6)
      return true;

    if (tokens[0] != ".A")
      return true;
    
    const double value(toDouble(tokens[5]));
    if (!finite(value))
      return true;
    
    const std::string& zone(tokens[3]);
    tZones::const_iterator zt(mZones.find(zone));
    
    if (zt == mZones.end()) {
      std::cerr << "Unrecognized zone(" << zone << ") in " << mURL << std::endl;
      return true;
    }
      
    const Tokenize fields(tokens[4], "/");
    
    if (fields.size() != 2)
      return true;

    if (fields[0].substr(0,2) != "DH") {
      std::cerr << "Unrecognized time prefix(" << fields[0] << ") in " << mURL << std::endl;
      return true;
    }

    tTypes::const_iterator it(mTypes.find(fields[1]));
    if (it == mTypes.end()) {
      std::cerr << "Unrecognized type(" << fields[1] << ") in " << mURL << std::endl;
      return true;
    }
    
    const std::string& station(tokens[1]);
    const std::string& date(tokens[2]);
    const time_t when(toDate(date + " " + fields[0].substr(2)));

    if (when != -1)
      dumpToDatabase(station, it->second, when + zt->second, value);
    
    return true;
  }
}
