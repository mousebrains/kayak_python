#ifndef INC_Parse_NOAA_XML_H_
#define INC_Parse_NOAA_XML_H_

#include <Parse.H>

class Curl;

namespace Parsers {
class NOAA_XML : public Parsers::Parse {
private:
  bool line(const std::string& l);

  const char *name() const {return "NOAA_XML";}
public:
  NOAA_XML(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_NOAA_XML_H_
