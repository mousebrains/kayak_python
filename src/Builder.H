#ifndef INC_Builder_H_
#define INC_Builder_H_

#include <vector>
#include <string>

class InfoDB;

class Builder {
public:
  class Record {
  private:
    std::string mUse;
    std::string mType;
    std::string mField;
    size_t mLength;
    std::string mText;
    std::string mHTML;

    bool mColUsed;

    const std::string& use() const {return mUse;}
    const std::string& type() const {return mType;}
  public:
    Record(const std::string& use, 
           const std::string& type,
           const std::string& field,
           const size_t length,
           const std::string& text,
           const std::string& html);

    bool colUsed() const {return mColUsed;}
    void colUsed(const bool flag) {mColUsed = flag;}

    bool use(const char a) const {return mUse.find(a) != mUse.npos;}
    bool type(const std::string a) const {return mType == a;}

    const std::string& field() const {return mField;}
    size_t length() const {return mLength;}
    const std::string& text() const {return mText;}
    const std::string& html() const {return mHTML;}

    friend std::ostream& operator << (std::ostream& os, const Record& r);
  };

private:
  typedef std::vector<Record> tRecords;
  tRecords mRecords;
public:
  Builder(InfoDB& info, const std::string& builderDB);

  typedef tRecords::const_iterator const_iterator;
  typedef tRecords::iterator iterator;
  typedef tRecords::size_type size_type;

  const_iterator begin() const {return mRecords.begin();}
  const_iterator end() const {return mRecords.end();}

  iterator begin() {return mRecords.begin();}
  iterator end() {return mRecords.end();}

  size_type size() const {return mRecords.size();}
  bool empty() const {return mRecords.empty();}

  const Record& operator [] (const size_type i) const {return mRecords[i];}
};

std::ostream& operator << (std::ostream& os, const Builder& r);

#endif // INC_Builder_H_
