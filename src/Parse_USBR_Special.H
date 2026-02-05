#ifndef INC_Parse_USBR_Special_H_
#define INC_Parse_USBR_Special_H_

#include <Parse.H>
#include <map>

class Curl;

namespace Parsers {
class USBR_Special : public Parsers::Parse {
private:
  typedef std::map<std::string, int> tZones;
  tZones mZones;
  
  typedef std::map<std::string, std::string> tTypes;
  tTypes mTypes;
  
  bool line(const std::string& l);

  const char *name() const {return "USBR_Special";}
public:
  USBR_Special(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_USBR_Special_H_
