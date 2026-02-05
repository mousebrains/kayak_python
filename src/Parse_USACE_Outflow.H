#ifndef INC_Parse_USACE_Outflow_H_
#define INC_Parse_USACE_Outflow_H_

#include <Parse.H>

class Curl;

namespace Parsers {
  class USACE_Outflow : public Parsers::Parse {
  private:
    int mState;
    std::string mProject;
    std::string mDate;
 
    bool line(const std::string& l);

    const char *name() const {return "USACE_Outflow";}
  public:
    USACE_Outflow(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db); 
  };
}

#endif // INC_Parse_USACE_Outflow_H_
