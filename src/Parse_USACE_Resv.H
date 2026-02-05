#ifndef INC_Parse_USACE_Resv_H_
#define INC_Parse_USACE_Resv_H_

#include <Parse.H>
#include <map>

class Curl;

namespace Parsers {
class USACE_Resv : public Parsers::Parse {
private:
  int mState;
  time_t mTime;

  bool mStacked;
  std::string mProject;
  double mFlow;
  double mGage;
 
  bool line(const std::string& l);

  const char *name() const {return "USACE_Resv";}
public:
  USACE_Resv(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_USACE_Resv_H_
