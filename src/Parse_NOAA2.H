#ifndef INC_Parse_NOAA2_H_
#define INC_Parse_NOAA2_H_

#include <Parse.H>
#include <TimeZone.H>

class Curl;

namespace Parsers {
  class NOAA2 : public Parsers::Parse {
  private:
    int mState;
    std::string mStation;
    struct tm mTM;
    TimeZone mTimeZone;

    bool line(const std::string& l);
    void getValue(const time_t& when, const std::string& field);
    std::string initStation(const std::string& url) const;

    const char *name() const {return "NOAA2";}
  public:
    NOAA2(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
  };
}

#endif // INC_Parse_NOAA2_H_
