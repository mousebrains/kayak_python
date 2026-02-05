#ifndef INC_Display_H_
#define INC_Display_H_

#include <Builder.H>
#include <ParameterDB.H>
#include <InfoDB.H>
#include <DataDB.H>
#include <map>
#include <set>
#include <vector>
#include <iosfwd>

class DataDB;

class Display {
public:
  class Record {
  private:
    typedef std::map<std::string, std::string> tMap;
    tMap mMap;
    std::string mBlank;
    time_t mTime;
  public:
    Record(const Builder& builder, const InfoDB::tRecord& record);

    const std::string& operator () (const std::string& key) const;

    void operator () (const std::string& key, const std::string& value);
    void operator () (const std::string& key, const time_t ts);

    const time_t& time() const {return mTime;}

    friend std::ostream& operator << (std::ostream& os, const Record& r);
  };

  typedef std::set<std::string> tStates;

  typedef std::vector<Record> tRecords;
  typedef tRecords::const_iterator const_iterator;

private:
  tRecords mRecords;
  ParameterDB mParams;
  InfoDB mInfo;
  DataDB mData;
  Builder mBuilder;
  tStates mStates;

  void getLatest(Record& record, time_t& ts, const DataDB::TYPE type, const std::string& id);
  bool getLatest(Record& record);
  std::string makeTableName(const std::string& state, const std::string& key);
  void addStates(const Record& record);
public:
  Display(const std::string& builderDB, const std::string& criteria);
  Display(const std::string& builderDB, const std::string& criteria, const std::string& hashes);

  void csv(const std::string& state);

  void text(const std::string& state);

  void html(const std::string& state);
  void html(const std::string& state, std::ostream& os, const bool fullPage, const bool tFoot);

  const tStates& states() {return mStates;}

  const_iterator begin() const {return mRecords.begin();}  
  const_iterator end() const {return mRecords.end();}  
};

#endif // INC_Display_H_
