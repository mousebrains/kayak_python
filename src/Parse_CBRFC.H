#ifndef INC_Parse_CBRFC_H_
#define INC_Parse_CBRFC_H_

#include <Tokenize.H>
#include <Parse.H>
#include <map>
#include <ctime>

class Curl;

namespace Parsers {
class CBRFC : public Parsers::Parse {
public:
  typedef std::map<std::string, Tokenize::size_type> tColumns;
private:
  int mState;
  struct tm mTM;

  tColumns mColumns;
  bool line(const std::string& l);
  time_t getTime(const Tokenize& tokens) const;
  double getDouble(const Tokenize& tokens, const std::string& field) const;
  const std::string& getString(const Tokenize& tokens, const std::string& field) const;

  const char *name() const {return "CBRFC";}
public:
  CBRFC(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_CBRFC_H_
