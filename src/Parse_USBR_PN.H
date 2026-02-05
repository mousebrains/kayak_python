#ifndef INC_Parse_USBR_PN_H_
#define INC_Parse_USBR_PN_H_

#include <Parse.H>
#include <DataDB.H>
#include <Tokenize.H>
#include <map>

class Curl;

namespace Parsers {
class USBR_PN : public Parsers::Parse {
private:
  int mState;

  typedef std::map<Tokenize::size_type, std::string> tStations;
  tStations mStations;
  
  typedef std::map<Tokenize::size_type, DataDB::TYPE> tTypes;
  tTypes mTypes;

  bool line(const std::string& l);

  const char *name() const {return "USBR_PN";}
public:
  USBR_PN(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_USBR_PN_H_
