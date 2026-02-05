#ifndef INC_Parse_NWRFC_H_
#define INC_Parse_NWRFC_H_

#include <Parse.H>
#include <Tokenize.H>

class Curl;

namespace Parsers {
class NWRFC : public Parsers::Parse {
private:
  std::string mStation;
  int mState;
  int mColGage;
  int mColFlow;
  int mColDate;
  int mColTime;
  Tokenize::size_type mMinCols;
  bool mqInflow;
  
  bool line(const std::string& l);
  std::string initStation(const std::string& url) const;

  const char *name() const {return "NWRFC";}
public:
  NWRFC(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_NWRFC_H_
