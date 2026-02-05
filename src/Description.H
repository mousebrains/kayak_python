#ifndef INC_Description_H_
#define INC_Description_H_

#include <vector>
#include <string>

class InfoDB;

class Description {
public:
  class Record {
  private:
    std::string mName;
    std::string mType;
    std::string mPrefix;
    std::string mSuffix;
  public:
    Record(const std::string& n, const std::string& t, const std::string& p, const std::string& s)
      : mName(n), mType(t), mPrefix(p), mSuffix(s) {}

    const std::string& name() const {return mName;}
    const std::string& type() const {return mType;}
    const std::string& prefix() const {return mPrefix;}
    const std::string& suffix() const {return mSuffix;}
  };

private:
  typedef std::vector<Record> tRecords;
  tRecords mRecords;
public:
  Description(InfoDB& info);

  typedef tRecords::const_iterator const_iterator;
  typedef tRecords::size_type size_type;

  const_iterator begin() const {return mRecords.begin();}
  const_iterator end() const {return mRecords.end();}
  size_type size() const {return mRecords.size();}
  bool empty() const {return mRecords.empty();}

  const Record& operator [] (const size_type i) const {return mRecords[i];}
};

std::ostream& operator << (std::ostream& os, const Description& r);
std::ostream& operator << (std::ostream& os, const Description::Record& r);

#endif // INC_Description_H_
