#ifndef INC_Parse_Waves_H_
#define INC_Parse_Waves_H_

#include <Parse.H>

class Curl;

namespace Parsers {
class Waves : public Parsers::Parse {
private:
  const std::string mStation;
  
  bool line(const std::string& l);

  const char *name() const {return "Waves";}
public:
  Waves(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_Waves_H_
