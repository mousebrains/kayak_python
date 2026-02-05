#ifndef INC_Parse_IDWR_H_
#define INC_Parse_IDWR_H_

#include <Parse.H>
#include <vector>

class Curl;

namespace Parsers {
class IDWR : public Parsers::Parse {
private:
  int mState;

  typedef std::vector<std::string::size_type> tColumns;
  tColumns mColumns;

  typedef std::vector<std::string> tTimes;
  tTimes mTimes;

  bool line(const std::string& l);
  std::string getField(const std::string& l, const tColumns::size_type i) const;

  const char *name() const {return "IDWR";}
public:
  IDWR(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_IDWR_H_
