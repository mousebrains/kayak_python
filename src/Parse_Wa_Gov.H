#ifndef INC_Parse_Wa_Gov_H_
#define INC_Parse_Wa_Gov_H_

#include <Parse.H>
#include <vector>

class Curl;

namespace Parsers {
class Wa_Gov : public Parsers::Parse {
private:
  int mState;
  DataDB::TYPE mType; // flow,temp,stage

  std::string mYear1;
  std::string mYear2;
  std::string mStation;

  typedef std::vector<std::string> tMonths;
  tMonths mMonths;

  typedef std::vector<std::string::size_type> tOffsets;
  tOffsets mOffsets;

  bool line(const std::string& l);

  const char *name() const {return "Wa_Gov";}
public:
  Wa_Gov(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_Wa_Gov_H_
