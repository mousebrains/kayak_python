#ifndef INC_URLparse_H_
#define INC_URLparse_H_

#include <vector>
#include <string>
#include <set>

class InfoDB;

class URLparse {
public:
  class Record {
  private:
    std::string mURL;
    std::string mParser;
    std::string mHours;

    typedef std::set<int> tSet;
    mutable tSet mHourSet;
    mutable tSet mHourExclude;
  public:
    Record(const std::string& url, const std::string& parser, const std::string& hours)
      : mURL(url), mParser(parser), mHours(hours) {}

    const std::string& url() const {return mURL;}
    const std::string& parser() const {return mParser;}
    const std::string& hours() const {return mHours;}

    bool qHour() const;
  };

private:
  typedef std::vector<Record> tRecords;
  tRecords mRecords;
public:
  URLparse(InfoDB& info, const std::string& urlCriteria, const std::string& parserCriteria);

  typedef tRecords::const_iterator const_iterator;
  typedef tRecords::size_type size_type;

  const_iterator begin() const {return mRecords.begin();}
  const_iterator end() const {return mRecords.end();}
  size_type size() const {return mRecords.size();}
  bool empty() const {return mRecords.empty();}

  const Record& operator [] (const size_type i) const {return mRecords[i];}
};

std::ostream& operator << (std::ostream& os, const URLparse& r);
std::ostream& operator << (std::ostream& os, const URLparse::Record& r);

#endif // INC_URLparse_H_
