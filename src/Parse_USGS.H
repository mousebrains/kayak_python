#ifndef INC_Parse_USGS_H_
#define INC_Parse_USGS_H_

#include <Parse.H>
#include <Tokenize.H>
#include <TimeZone.H>
#include <map>
#include <set>

class Curl;

namespace Parsers {
  class USGS : public Parsers::Parse {
  private:
    int mState;
    TimeZone mTimeZone;
  
    Tokenize::tTokens mColumns;
  
    typedef std::pair<std::string::size_type, std::string::size_type> tPair;
    typedef std::map<std::string, tPair> tMap;
    tMap mFields;
  
    std::string getString(const Tokenize& tokens, const std::string& field) const;
    bool line(const std::string& l);

    const char *name() const {return "USGS";}
  public:
    USGS(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
  };
}

#endif // INC_Parse_USGS_H_
