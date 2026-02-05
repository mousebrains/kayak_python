#ifndef INC_Parse_NOAA_H_
#define INC_Parse_NOAA_H_

#include <Parse.H>

class Curl;

namespace Parsers {
  class NOAA : public Parsers::Parse {
  private:
    int mState;
    std::string mStation;
    std::string::size_type mColIndex;
    struct tm mTM;

    bool line(const std::string& l);
    void getValue(const time_t& when, const std::string& field);
    std::string initStation(const std::string& url) const;

    const char *name() const {return "NOAA";}
  public:
    NOAA(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
  };
}

#endif // INC_Parse_NOAA_H_
