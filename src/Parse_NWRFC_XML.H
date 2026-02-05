#ifndef INC_Parse_NWRFC_XML_H_
#define INC_Parse_NWRFC_XML_H_

#include <Parse.H>

class Curl;

namespace Parsers {
class NWRFC_XML : public Parsers::Parse {
private:
  bool line(const std::string& l);

  std::string mStation;
  int mState;
  time_t mTime;
  time_t mNow;
  bool mqHeight, mqFlow;
  double mHeight, mFlow;

  const char *name() const {return "NWRFC_XML";}
public:
  NWRFC_XML(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_NWRFC_XML_H_
