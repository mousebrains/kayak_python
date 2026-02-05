#ifndef INC_Parse_USBR_H_
#define INC_Parse_USBR_H_

#include <Parse.H>
#include <DataDB.H>
#include <Tokenize.H>
#include <map>

class Curl;

namespace Parsers {
class USBR : public Parsers::Parse {
private:
  int mState;

  struct Info {
    std::string station;
    DataDB::TYPE type;
    double offset;
    double scaling;

    Info(const std::string& name, const DataDB::TYPE& ty, const double off=0, const double norm=1)
      : station(name), type(ty), offset(off), scaling(norm) {}

    Info(const std::string& name, const Info& rhs)
      : station(name), type(rhs.type), offset(rhs.offset), scaling(rhs.scaling) {}

    Info(const DataDB::TYPE& ty, const double off=0, const double norm=1)
      : station(""), type(ty), offset(off), scaling(norm) {}

    double normalize(const double val) const {
      return (scaling == 1) ? val : (val * scaling + offset);
    }
  }; // Info

  typedef std::map<Tokenize::size_type, Info> tColumns;
  tColumns mColumns;
  
  bool line(const std::string& l);

  const char *name() const {return "USBR";}
public:
  USBR(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
};
}

#endif // INC_Parse_USBR_H_
