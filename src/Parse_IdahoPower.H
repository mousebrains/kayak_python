#ifndef INC_Parse_IdahoPower_H_
#define INC_Parse_IdahoPower_H_

#include <Parse.H>
#include <vector>

class Curl;

namespace Parsers {
class IdahoPower : public Parsers::Parse {
private:
  bool line(const std::string& l);

  const char *name() const {return "IdahoPower";}
public:
  IdahoPower(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_IdahoPower_H_
