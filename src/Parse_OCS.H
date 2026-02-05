#ifndef INC_Parse_OCS_H_
#define INC_Parse_OCS_H_

#include <Parse.H>
#include <map>

class Curl;

namespace Parsers {
class OCS : public Parsers::Parse {
private:
  int mState;
  time_t mTime;
  std::string::size_type mColLocation;
  std::string::size_type mFlow;
  std::string::size_type mStage;
  std::string::size_type mChange;
  std::string::size_type mEnd;

  std::string mName;
 
  bool line(const std::string& l);

  const char *name() const {return "NWRFC_OCS";}
public:
  OCS(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_OCS_H_
