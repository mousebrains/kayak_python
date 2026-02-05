#include <DataDB.H>
#include <Tokenize.H>
#include <String.H>
#include <sstream>

// #define QueryPrint

namespace {
  const std::string URL2Name("url2name");
  const std::string latestName("Latest"); 
}

class TypeMap {
private:
  typedef std::map<std::string, DataDB::TYPE> tStrMap;
  mutable tStrMap mStrMap;

  void init() const {
    mStrMap.insert(std::make_pair("flow", DataDB::FLOW));
    mStrMap.insert(std::make_pair("inflow", DataDB::INFLOW));
    mStrMap.insert(std::make_pair("outflow", DataDB::OUTFLOW));
    mStrMap.insert(std::make_pair("gauge", DataDB::GAGE));
    mStrMap.insert(std::make_pair("gage", DataDB::GAGE));
    mStrMap.insert(std::make_pair("temperature", DataDB::TEMPERATURE));
    mStrMap.insert(std::make_pair("*", DataDB::ALL));
  }
public:
  TypeMap() {}

  const std::string operator () (const DataDB::TYPE type) const {
    switch (type) {
      case DataDB::FLOW: return "flow";
      case DataDB::INFLOW: return "inflow";
      case DataDB::OUTFLOW: return "outflow";
      case DataDB::GAGE: return "gauge";
      case DataDB::TEMPERATURE: return "temperature";
      case DataDB::ALL: return "*";
      case DataDB::UNKNOWN: return "GotMe";
    }
    return "GotMe";
  }

  DataDB::TYPE operator () (const std::string& str) const {
    if (mStrMap.empty()) init();

    tStrMap::const_iterator it(mStrMap.find(String::toLower(str)));
    if (it != mStrMap.end())
      return it->second;

    std::cerr << "Unrecognized type string(" << str << ")" << std::endl;
    return DataDB::UNKNOWN;
  }
};

namespace { TypeMap typeMap; }

void
DataDB::wrapup()
{
  for (tData::const_iterator et(mData.end()), it(mData.begin()); it != et; ++it) {
    const std::string& table(it->first);
    const tDatum& records(it->second);
    // Always try and create the table, since I've had problems on thinking one exists
    mQuery << "create table if not exists " << table
           << " (time timestamp DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP key"
           << ", value text not null);";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
    mQuery.execute();

    mQuery << "replace into " << table << " values ";
    std::string delim;
    time_t latestTime(0);

    for (tDatum::const_iterator jte(records.end()), jt(records.begin()); jt != jte; ++jt) {
      mQuery << delim << "(from_unixtime(" << jt->first << ")," << jt->second << ")";
      delim = ",";
      if (jt->first > latestTime) {
        latestTime = jt->first;
      }
    }
    mQuery << ";"; //  on duplicate key update value=values(value);";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
    mQuery.execute();

    updateLatest(table);
  }

  if (!mName2URL.empty()) {
    if (!mDB.tableExists(URL2Name, true)) {
      mQuery << "create table if not exists " << URL2Name
             << " (row int key auto_increment, "
             << "time timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, url text not null, name text not null);";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
      mQuery.execute();
    }

    for (tName2URL::const_iterator et(mName2URL.end()), it(mName2URL.begin()); it != et; ++it) {
      const std::string& name(it->first);
      const std::string& url(it->second);
      mQuery << "select `row` from " << URL2Name 
             << " where url=" << mQuery.quote(url) 
             << " and name=" << mQuery.quote(name)
             << ";";
      const MyQuery::Result result(mQuery.store());
      const std::string row(result.size() ? result.at(0).at(0).c_str() : "null");
      mQuery << "insert into " << URL2Name << " values (" 
             << row << ", now(), " 
             << mQuery.quote(url) << ", " 
             << mQuery.quote(name)
             << ") on duplicate key update time=now();";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
      mQuery.execute();
    }
  }
  mData.clear();
}

void
DataDB::updateLatest(const std::string& dbname,
                     const TYPE type)
{
  updateLatest(tableName(dbname, type));
}

void
DataDB::updateLatest(const std::string& table)
{
  const std::string eTime("(select max(time) from " + table + ")");
  const std::string eValue("(select value from " + table + " where time=" + eTime + ")");
  const std::string interval(" where time < " + eTime + " - interval 1 hour and time > " + 
                             eTime + " - interval 25 hour");
  const std::string sTime("(select max(time) from " + table + interval + ")");
  const std::string sValue("(select value from " + table + " where time=" + sTime + ")");

  if (!mDB.tableExists(latestName, true)) { // Create a latest table
    mQuery << "create table " << latestName
	   << "(name varchar(255) binary key comment 'Table name'," 
	   << " time timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP comment 'Time of measurement',"
	   << " value float comment 'Data value',"
           << " prevTime timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP comment 'Time of previous measurement',"
           << " prevValue float comment 'Previous data value',"
           << " delta float comment 'Rate of change/hour'"
           << ");";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
    mQuery.execute();
  }

  mQuery << "replace into " << latestName << " values (" 
         << mQuery.quote(table) << ","
         << eTime << "," << eValue << ","
         << sTime << "," << sValue << ","
         << "(" << eValue << " - " << sValue << ") / " << sValue << " / "
         << "((unix_timestamp(" << eTime << ") - unix_timestamp(" << sTime << ")) / 3600)"
         << ");";

#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
  mQuery.execute();
}

DataDB::TYPE DataDB::type(const std::string& t) { return typeMap(t); }
std::string DataDB::type(const DataDB::TYPE t) { return typeMap(t); }
std::string DataDB::tableName(const std::string& name, const DataDB::TYPE type) { return typeMap(type) + "_" + name; }
std::string DataDB::tableName(const std::string& name, const std::string& type) { return tableName(name, typeMap(type)); }

bool
DataDB::latest(const std::string& name,
               const enum TYPE type,
               const std::string& criteria,
               time_t& when,
               double& value,
               bool& changeFlag,
               double& change)
{
  return latest(tableName(name, type), criteria, when, value, changeFlag, change);
}

bool
DataDB::latest(const std::string& tableName,
               const std::string& criteria,
               time_t& when,
               double& value,
               bool& changeFlag,
               double& change)
{
  if (!mDB.tableExists(latestName, false)) 
    return false;

  mQuery << "select unix_timestamp(time),value,delta from " << latestName 
         << " where name=" << mQuery.quote(tableName) 
         << (criteria.empty() ? "" : " and ") << criteria
         << ';';

  const MyQuery::Result result(mQuery.store());

  if (result.empty())
    return false;

  const MyQuery::Row row(result.at(0));

  if (row.at(0).is_null() || row.at(1).is_null())
    return false;

  when = (time_t) row.at(0);
  value = (double) row.at(1);
  changeFlag = !row.at(2).is_null();
  change = changeFlag ? (double) row.at(2) : 0;
  return true;
}

const DataDB::tRecords&
DataDB::records(const std::string& name,
                const TYPE type,
                const std::string criteria)
{
  return records(tableName(name, type), criteria);
}

const DataDB::tRecords&
DataDB::records(const std::string& tableName,
                const std::string criteria)
{
  mRecords.clear();

  mQuery << "select unix_timestamp(time),value from "
         << tableName
         << " where time is not null and value is not null" 
         << (criteria.empty() ? "" : " and ") << criteria
         << " order by time desc;";

  const MyQuery::Result result(mQuery.store());

  for (MyQuery::Result::size_type size(result.size()), i(0); i < size; ++i) {
    const MyQuery::Row row(result.at(i)); 
    mRecords.push_back(Datum((time_t) row.at(0), row.at(1).c_str()));
  }

  return mRecords;
}

bool
DataDB::operator () (const std::string& name,
                     const time_t when,
                     const enum TYPE type,
                     double value)
{
  const time_t now(time(0));

  if (when > now) {
    // std::cerr << "Rejected database update to " << name
              // << " since timestamp supplied, "
              // << Convert::toString(when) << ", is in the future. Now("
              // << Convert::toString(now) << ")" << std::endl;
    // return false;
    return true;
  }

  if ((type == FLOW) && ((value = (double)((int) value)) < 0)) {
    std::cerr << "Rejected database update to " << name 
              << " since the flow(" << value << ") is negative at "
              << ctime(&when) 
              << std::endl;
    return false;
  }

  const std::string table(tableName(name, type));
  tData::iterator it(mData.find(table));
  if (it == mData.end()) {
    tDatum records;
    records.insert(std::make_pair(when, value));
    mData.insert(std::make_pair(table, records));
    it = mData.find(table);
  } else {
    it->second.insert(std::make_pair(when, value));
  }

  return true;
}

bool
DataDB::merge(const std::string& tgtName,
              const Tokenize& srcNames,
              const TYPE type,
              const std::string& criteria)
{
  std::string delim(" ");
  std::string msg;
  std::string refTable;

  typedef std::set<std::string> tDBs;
  tDBs dbs;

  for (Tokenize::const_iterator et(srcNames.end()), it(srcNames.begin()); it != et; ++it) {
    const std::string srcTable(tableName(*it, type));
    if (mDB.tableExists(srcTable, false)) {
      dbs.insert(*it);

      msg += delim + "select * from " + srcTable + (criteria.empty() ? "" : " ") + criteria;
      delim = " union ";
      if (refTable.empty()) 
        refTable = srcTable;
    }
  }

  if (!refTable.empty()) {
    const std::string tgtTable(tableName(tgtName, type));
    bool qCreate(!mDB.tableExists(tgtTable, true));
    if (qCreate) {
      mQuery << "create table " << tgtTable << " like " << refTable << ";";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
      mQuery.execute();
    }

    mQuery << "insert ignore into " << tgtTable << msg << ";";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
    mQuery.execute();

    if (qCreate) { // Check that there are records
      const DataDB::tRecords& recs(records(tgtTable, std::string()));
      if (recs.empty()) { // Nothing in table, so drop it and skip the rest
        dropTable(tgtTable);
        return true;
      }
    }

    updateLatest(tgtName, type);

    if (mDB.tableExists(URL2Name, false)) {
      for (tDBs::const_iterator et(dbs.end()), it(dbs.begin()); it != et; ++it) {
        const std::string& name(*it);
        mQuery << "select url,time from " << URL2Name
               << " where name=" << mQuery.quote(name) << ";";
        const MyQuery::Result result(mQuery.store());
  
        for (MyQuery::Result::size_type size(result.size()), i(0); i < size; ++i) {
          const std::string url(result.at(i).at(0).c_str());
          const std::string time(result.at(i).at(1).c_str());
          mQuery << "select `row` from " << URL2Name 
                 << " where url=" << mQuery.quote(url) 
                 << " and name=" << mQuery.quote(tgtName)
                 << ";";
          const MyQuery::Result result(mQuery.store());
          const std::string row(result.size() ? result.at(0).at(0).c_str() : "null");
          mQuery << "insert into " << URL2Name << " values (" 
                  << row 
                  << "," << mQuery.quote(time) 
                  << "," << mQuery.quote(url) 
                  << ", " << mQuery.quote(tgtName)
                  << ") on duplicate key update time=" << mQuery.quote(time) << ";";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
          mQuery.execute();
        }
      }
    }
  }

  return true;
}

bool
DataDB::replace(const std::string& tgtName,
                const TYPE type,
                const std::string& values)
{
  const std::string table(tableName(tgtName, type));

  if (!mDB.tableExists(table, true)) {
    mQuery << "create table if not exists " << table
           << " (time timestamp DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP key, value text not null);";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
    mQuery.execute();
  }

  mQuery << "replace into " << table << " values (" << values << ");";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
  mQuery.execute();

  return mQuery;
}

bool
DataDB::url(const std::string& url,
            const std::string& name)
{
  tName2URL::iterator it(mName2URL.find(name));

  if (it == mName2URL.end()) 
    mName2URL.insert(std::make_pair(name, url));

  return true;
}

DataDB::tURLs
DataDB::url(const std::string& name)
{
  const std::string& tableName(URL2Name);
  tURLs urls;

  if (!mDB.tableExists(tableName, false)) 
    return urls;

  mQuery << "select url from " << tableName
         << " where name=" << mQuery.quote(name) << ";";
  const MyQuery::Result result(mQuery.store());
  
  for (MyQuery::Result::size_type size(result.size()), i(0); i < size; ++i) 
    urls.push_back(result.at(i).at(0).c_str());
  
  return urls;
}

DataDB::tNamesURLs
DataDB::namesAndURLs()
{
  tNamesURLs a;

  mQuery << "select name,url from " << URL2Name << ";";
  const MyQuery::Result result(mQuery.store());
  
  for (MyQuery::Result::size_type size(result.size()), i(0); i < size; ++i) 
    a.insert(std::make_pair(result.at(i).at(0).c_str(),
			    result.at(i).at(1).c_str()));

  return a;
}

std::ostream&
operator << (std::ostream& os,
             const DataDB::Datum& d)
{
  os << d.value << ' ' << ctime(&d.when);
  return os;
}

std::ostream&
operator << (std::ostream& os,
             const DataDB::TYPE& t)
{
  os << typeMap(t);
  return os;
}

static std::string
mkRatingTableName(const std::string& dbName)
{
  return ("levels_page." + dbName + "_rt");
}

DataDB::tRatingTable 
DataDB::getRatingTable(const std::string& dbName)
{
  tRatingTable rt;
  const std::string tableName(mkRatingTableName(dbName));

  mQuery << "select feet,cfs from " << tableName << ";";

  const MyQuery::Result result(mQuery.store());

  for (MyQuery::Result::size_type i(0), e(result.size()); i < e; ++i) {
    const MyQuery::Row row(result.at(i));
    rt.insert(std::make_pair(row.at(0), row.at(1)));
  }

  return (rt);
}

void 
DataDB::putRatingTable(const std::string& dbName, 
		       const tRatingTable& rt)
{
  const std::string tableName(mkRatingTableName(dbName));

  mQuery << "drop table if exists " << tableName << ";";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
  mQuery.execute();

  mQuery << "create table if not exists " << tableName
	 << " (feet double, cfs double);";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
  mQuery.execute();

  mQuery << "replace into " << tableName << " values ";

  std::string delim;

  for (tRatingTable::const_iterator it(rt.begin()), et(rt.end()); it != et; ++it) {
    mQuery << delim << "(" << it->first << "," << it->second << ")";
    delim = ",";
  }
  mQuery << ";";
#ifdef QueryPrint
std::cout << "QUERY: " << mQuery.str() << std::endl;
#endif // QueryPrint
  mQuery.execute();
}
