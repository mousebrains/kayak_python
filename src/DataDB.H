#ifndef INC_DataDB_H_
#define INC_DataDB_H_

#include <MyDB.H>
#include <Paths.H>
#include <Convert.H>
#include <vector>
#include <set>

class Tokenize;

class DataDB {
public:
  enum TYPE {
    FLOW,
    INFLOW,
    OUTFLOW,
    GAGE,
    TEMPERATURE,
    ALL,
    UNKNOWN
  };

  class Datum {
  public:
    time_t when;
    std::string  value;

    Datum() : when(0) {}
    Datum(const time_t w, const std::string& v) : when(w), value(v) {}
  };

  typedef std::vector<Datum> tRecords;

  typedef std::map<double, double> tRatingTable;

private:
  MyDB mDB;
  MyQuery& mQuery; // For output

  tRecords mRecords;

  typedef std::map<time_t, double> tDatum;
  typedef std::map<std::string, tDatum > tData;
  tData mData;

  typedef std::map<std::string, std::string> tName2URL;
  tName2URL mName2URL;

  void updateLatest(const std::string& table);
public:
  DataDB() : mDB(Paths::MySQLDataDB), mQuery(mDB.query()) {}

  void wrapup();

  bool latest(const std::string& name, 
              const enum TYPE type,
              const std::string& criteria,
              time_t& when,
              double& value,
              bool& changeSet,
              double& change);

  bool latest(const std::string& dbname, 
              const std::string& criteria,
              time_t& when,
              double& value,
              bool& changeSet,
              double& change);

  const tRecords& records(const std::string& name, 
                          const enum TYPE type, 
                          const std::string criteria = "now() - interval 60 day");

  const tRecords& records(const std::string& tableName, const std::string criteria);

  tRatingTable getRatingTable(const std::string& dbName);
  void putRatingTable(const std::string& dbName, const tRatingTable& rt);

  bool operator () (const std::string& name, const enum TYPE type, const Datum& datum) {
    return (*this)(name, datum.when, type, Convert::strTo<double>(datum.value));
  }

  bool operator () (const std::string& name, 
                    const time_t when, 
                    const enum TYPE type, 
                    double value);

  bool operator () (const std::string& name, 
                    const time_t when, 
                    const std::string& t, 
                    const double value) {
    return (*this)(name, when, type(t), value);
  }

  bool url(const std::string& url, const std::string& name);

  typedef std::vector<std::string> tURLs;

  tURLs url(const std::string& name);

  typedef std::multimap<std::string, std::string> tNamesURLs;
  tNamesURLs namesAndURLs();

  bool tableExists(const std::string& tableName) { return mDB.tableExists(tableName, false); }
  bool tableExists(const std::string& name, const enum TYPE type, const bool willCreate) {
    return mDB.tableExists(tableName(name, type), willCreate);
  }

  const MyDB::tTables& tables() {return mDB.tables();}

  bool dropTable(const std::string& tableName) {return mDB.dropTable(tableName);}
  bool deleteRows(const std::string& tableName, const std::string& criteria) {
    return mDB.deleteRows(tableName, criteria);
  }

  bool merge(const std::string& targetName, 
             const Tokenize& srcTables, 
             const TYPE type,
             const std::string& criteria = "");

  bool replace(const std::string& targetName,
               const TYPE type,
               const std::string& values);

  void updateLatest(const std::string& targetName, const TYPE type);

  static std::string tableName(const std::string& key, const enum TYPE type);
  static std::string tableName(const std::string& key, const std::string& type);

  static enum TYPE type(const std::string& t);
  static std::string type(const enum TYPE t);
};

std::ostream& operator << (std::ostream& os, const DataDB::Datum& d);
std::ostream& operator << (std::ostream& os, const DataDB::TYPE& t);

#endif // INC_DataDB_H_
