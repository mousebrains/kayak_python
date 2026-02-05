#ifndef INC_InfoDB_H_
#define INC_InfoDB_H_

#include <MyDB.H>
#include <Paths.H>
#include <set>
#include <map>
#include <vector>

class InfoDB {
public:
  typedef std::map<std::string, std::string> tCorrections;
  typedef std::vector<std::string> tRecord;
  typedef std::vector<tRecord> tRecords;
  typedef std::set<std::string> tStates;
private:
  MyDB mDB;
  MyQuery& mQuery;

  tRecords mRecords;
  tStates mStates;

  void mkMergedMaster(const std::string& tableName);
public:
  InfoDB() : mDB(Paths::MySQLInfoDB), mQuery(mDB.query()) {}

  void mkMergedMaster(); // Force rengeneration of MergedMaster

  time_t lastUpdate();

  const tStates& allStates();

  const tRecords& masterColumns();

  const tRecords& master(const std::string& colNames = "*", const std::string& criteria = "");
  const tRecords& edit(const std::string& colNames);

  MyQuery& query() {return mQuery;}

  std::string newHash();

  bool corrections(const std::string& hashValue,
                   const std::string& userName,
                   const std::string& email,
                   const tCorrections& corr,
		   const std::string& key);

  bool authenticate(const std::string& hashValue,
		    const std::string& key);

  void cleanOutCorrections(const time_t before);

  std::string displayName(const std::string& hash);
};

std::ostream& operator << (std::ostream& os, const InfoDB::tRecord& r);
std::ostream& operator << (std::ostream& os, const InfoDB::tRecords& r);

#endif // INC_InfoDB_H_
