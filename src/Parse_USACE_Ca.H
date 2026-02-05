#ifndef INC_Parse_USACE_Ca_H_
#define INC_Parse_USACE_Ca_H_

#include <Parse.H>
#include <DataDB.H>
#include <vector>

class Curl;
class Tokenize;

namespace Parsers {
  class USACE_Ca : public Parsers::Parse {
  private:
    int mState;
    std::string mNamePrefix;
    
    typedef std::vector<std::string> tLines;
    tLines mLines;
   
    typedef std::vector<std::string> tNames;
    tNames mNames;

    typedef std::vector<DataDB::TYPE> tTypes;
    tTypes mTypes;

    typedef std::pair<std::string::size_type, std::string::size_type> tPair;
    typedef std::vector<tPair> tIndices;
    tIndices mIndices;
 
    bool line(const std::string& l);

    std::string::size_type handleInOutFlow(const std::string::size_type index,
                                           std::string::size_type offset,
                                           const std::string& l);

    void parseHeader(const std::string& l, const Tokenize& tokens);
    std::string extractName(const std::string::size_type start, const std::string::size_type stop);

    const char *name() const {return "USACE_Ca";}
  public:
    USACE_Ca(const Curl& curl, const bool qVerbose, const bool qDryRun, DataDB& db);
  };
}

#endif // INC_Parse_USACE_Ca_H_
