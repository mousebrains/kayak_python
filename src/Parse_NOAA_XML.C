#include <Parse_NOAA_XML.H>
#include <File.H>
#include <Tokenize.H>
#include <DataDB.H>
#include <String.H>
#include <XML.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  NOAA_XML::NOAA_XML(const Curl& curl,
                     const bool qVerbose,
                     const bool qDryRun,
		     DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db)
  {
    try {
      const std::string contentType(curl.contentType());
      if (!contentType.empty() && 
          (contentType.find("text/xml") != 0) &&
          (contentType.find("text/html") != 0)) {
        std::cerr << "Invalid content type(" << contentType 
                  << ") for NOAA_XML url(" << mURL << ")" << std::endl;
std::cerr << curl.str() << std::endl;
        throw "Invalid content type(" + contentType + 
              ") for NOAA_XML url(" + mURL + ")";
      }

      XML xml(curl.str(), mURL);

      const std::string id(xml.root().attribute("id"));
  
      time_t when(0);
  
      for (XML::const_iterator it = xml.begin(); it != xml.end(); ++it) {
        const std::string path(it->path());
        if (path.find("/observed/datum") != path.npos) {
          const std::string timezone(it->attribute("timezone"));
          if (!timezone.empty()) { // timezone, so this must be a timestamp
            if (!(when = toDate(it->content(), timezone, true))) 
              std::cerr << "Error converting '" << it->content() << "' into a date" << std::endl;
          } else if(when) {
            const std::string units(it->attribute("units"));
            if (!units.empty()) { // A measurement
              const std::string value(it->content());
              if (!value.empty()) {
                const double val(Convert::strTo<double>(value));
                if (finite(val)) {
                  if (units == "ft") {
                    dumpToDatabase(id, DataDB::GAGE, when, val);
		  } else if (units == "cfs") {
                    if ((val >= 0) && (val <= 2e6))
                      dumpToDatabase(id, DataDB::FLOW, when, val);
		  } else if (units == "kcfs") {
                    if ((val >= 0) && (val <= 2e6))
                      dumpToDatabase(id, DataDB::FLOW, when, val * 1000);
		  } else
                    std::cerr << "Unrecognized units(" << units << ") for " << id << std::endl;
                }
              }
            }
          }
        }
      } 
    } catch (std::exception& e) {
      std::cerr << "Error processing " << curl.url() << " in NOAA_XML, " << e.what() << std::endl;
    } 
  }

  bool
  NOAA_XML::line(const std::string& l)
  {
    Tokenize tokens(l);

    if (mDebug)
      std::cout << l << std::endl;

    return true;
  }
}
