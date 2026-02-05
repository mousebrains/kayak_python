#include <InfoDB.H>
#include <Tokenize.H>

namespace {
  const std::string masterName("Master");
  const std::string correctionsName("Corrections");
  const std::string mergedMasterName("Merged" + masterName);


  InfoDB::tRecords& storeRecords(MyQuery& query, InfoDB::tRecords& rec) {
    const MyQuery::Result result(query.store());

    for (MyQuery::Result::size_type i = 0; i < result.size(); ++i) {
      MyQuery::Row row(result.at(i));
      InfoDB::tRecord record;
      for (MyQuery::Row::size_type j = 0; j < row.size(); ++j) 
        record.push_back(row.at(j).is_null() ? "" : row.at(j).c_str());
      rec.push_back(record);
    }
    return rec;
  }

  MyQuery::Result doQuery(MyQuery& query, 
                       const std::string& fields, 
                       const std::string& criteria)
  {
    query << "select " << fields << " from " << masterName
          << (criteria.empty() ? "" : " where ") << criteria
          << " union "
          << "select " << fields << " from " << correctionsName
          << (criteria.empty() ? "" : " where ") << criteria
          << ";";
    return query.store();
  }

  std::string makeHash(std::string::size_type i) {
    const std::string symbols("0123456789abcdefghijklmnopqrstuvwxyz");
    std::string hash(symbols.substr(i % symbols.size(), 1));

    for (i /= symbols.size();i; i /= symbols.size()) 
      hash += symbols.substr(i % symbols.size(), 1);

    return hash;
  }
}

time_t
InfoDB::lastUpdate()
{
  mQuery << "select unix_timestamp(max(date)) as t1 from " << masterName
         << " union "
         << "select unix_timestamp(max(date)) from " << correctionsName
         << " order by t1 desc limit 1;";

  const MyQuery::Result result(mQuery.store());

  if (result.size() != 1) {
    std::cerr << "Error getting alst update time, not exactly one entry, " 
              << result.size() << std::endl;
    std::cerr << result;
    throw std::logic_error("Error getting last update time, not exactly one entry");
  }

  return result.at(0).at(0);
}

const InfoDB::tStates&
InfoDB::allStates()
{
  mStates.clear();

  const MyQuery::Result result(doQuery(mQuery, "state", "state is not null")); 

  static std::set<std::string> toExclude;
  if (toExclude.empty()) {
    toExclude.insert("New_Mexico");
    toExclude.insert("rpYmakNdYCWyu");
    toExclude.insert("XvYbTYGnVvpRWcq");
  }

  for (MyQuery::Result::size_type i = 0; i < result.size(); ++i) {
    MyQuery::Row row(result.at(i));
    Tokenize tokens(row.at(0));
    for (Tokenize::const_iterator it = tokens.begin(); it != tokens.end(); ++it) {
      if (toExclude.find(*it) == toExclude.end()) {
        mStates.insert(*it);
      }
    }
  }

  return mStates;
}

void
InfoDB::mkMergedMaster()
{
  mkMergedMaster(mergedMasterName);
}

void
InfoDB::mkMergedMaster(const std::string& tableName)
{
  // Make a fresh copy of MergedMaster, with what is in master

  mQuery << "drop table if exists " << tableName << ";";
  mQuery.execute();

  mQuery << "create table " << tableName << " like " << masterName << ";";
  mQuery.execute();

  mQuery << "insert into " << tableName 
         << " select * from " << masterName
         << " where hashValue is not null;";

  mQuery.execute();

  // Get column names

  tRecord names; // Column names
  {
    mQuery << "describe " << tableName << ";";
    const MyQuery::Result result(mQuery.store());
    for (MyQuery::Result::size_type i = 0; i < result.size(); ++i) {
      const MyQuery::Row row(result.at(i));
      if (row.size() > 0) {
        names.push_back(row.at(0).c_str());
      }
    }
  }
 
  // Get all the corrections records

  mQuery << "select * from " << correctionsName
         << " where hashValue is not null and approved is not null;";

  const MyQuery::Result result(mQuery.store());

  for (MyQuery::Result::size_type i = 0; i < result.size(); ++i) {
    const MyQuery::Row row(result.at(i));
    mQuery << "insert into " << tableName << " (";
    std::string delim;
    for (MyQuery::Row::size_type j = 0; j < row.size(); ++j) {
      const MyQuery::ColData value(row[j]);
      if (!value.is_null()) {
        mQuery << delim << names[j];
        delim = ",";
      }
    }
    if (delim.empty()) {
      mQuery.reset();
      continue;
    }
    mQuery << ") values (";
    delim.clear();
    for (MyQuery::Row::size_type j = 0; j < row.size(); ++j) {
      const MyQuery::ColData value(row[j]);
      if (!value.is_null()) {
        mQuery << delim << mQuery.quote(value.c_str());
        delim = ",";
      }
    }
    mQuery << ") on duplicate key update ";
    delim.clear();
    for (MyQuery::Row::size_type j = 0; j < row.size(); ++j) {
      const MyQuery::ColData value(row[j]);
      if (!value.is_null()) {
        mQuery << delim << names[j] << "=values(" << names[j] << ")";
        delim = ",";
      }
    }
    mQuery << ";";
    mQuery.execute();
  }
}

bool 
InfoDB::corrections(const std::string& hashValue,
                    const std::string& userName,
                    const std::string& email,
                    const tCorrections& corr,
		    const std::string& key)
{
  mQuery << "insert into " << correctionsName 
	 << " (hashValue,userName,email,randomKey";

  for (tCorrections::const_iterator it = corr.begin(); it != corr.end(); ++it)
    if (!it->second.empty()) 
      mQuery << "," << it->first;

  mQuery << ") values ("
         << mQuery.quote(hashValue)
         << "," << mQuery.quote(userName)
         << "," << mQuery.quote(email)
         << "," << mQuery.quote(key);

  for (tCorrections::const_iterator it = corr.begin(); it != corr.end(); ++it)
    if (!it->second.empty())
      mQuery << "," << mQuery.quote(it->second);

  mQuery << ");";

  mQuery.execute();

  return true;
}

bool
InfoDB::authenticate(const std::string& hashValue,
		     const std::string& key)
{
  mQuery << "update " << correctionsName
	 << " set approved=" << "'1'"
	 << " where randomKey=" << mQuery.quote(key)
         << " and hashValue=" << mQuery.quote(hashValue)
	 << ";";
  mQuery.execute();

  mkMergedMaster(mergedMasterName);

  return true;
}

void
InfoDB::cleanOutCorrections(const time_t before)
{
  mQuery << "delete from " << correctionsName
	 << " where unix_timestamp(date) < " << before << ";";
  mQuery.execute();
}

std::string
InfoDB::displayName(const std::string& hash)
{
  if (!mDB.tableExists(mergedMasterName, true))
    mkMergedMaster(mergedMasterName);

  mQuery << "select display_name,gauge_location from " 
	 << mergedMasterName 
	 << " where hashValue=" << mQuery.quote(hash) 
	 << ";";

  tRecords records;
  storeRecords(mQuery, records);
  if (records.size() == 1) {
    const std::string& name(records[0][0]);
    const std::string& loci(records[0][1]);
    return name + (loci.empty() ? "" : ("@" + loci));
  }
  return std::string();
}

const InfoDB::tRecords&
InfoDB::master(const std::string& fields,
               const std::string& criteria)
{
  if (!mDB.tableExists(mergedMasterName, true))
    mkMergedMaster(mergedMasterName);
  
  mRecords.clear();

  mQuery << "select " << fields << " from " << mergedMasterName
         << " where sort_key is not null"
         << (criteria.empty() ? "" : " and ") << criteria
         << " order by sort_key;";

  return storeRecords(mQuery, mRecords);
}

const InfoDB::tRecords&
InfoDB::edit(const std::string& fields)
{
  const std::string tblName("Edit");

  mRecords.clear();

  if (!mDB.tableExists(tblName, false))
    return mRecords;

  mQuery << "select " << fields << " from " << tblName << " order by sortKey;";

  return storeRecords(mQuery, mRecords);
}

const InfoDB::tRecords&
InfoDB::masterColumns()
{
  mRecords.clear();
  mQuery << "describe " << masterName << ";";
  return storeRecords(mQuery, mRecords);
}

std::string
InfoDB::newHash()
{
  mQuery << "select hashValue from MergedMaster";

  tRecords records;

  storeRecords(mQuery, records);
  
  typedef std::set<std::string> tHashes;
  tHashes hashes;
  for (tRecords::size_type i = 0; i < records.size(); ++i)
    hashes.insert(records[i][0]);

  for (int i = hashes.size(); ; ++i) {
    const std::string hash(makeHash(i));
    if (hashes.find(hash) == hashes.end()) 
      return hash;
  }
  return std::string();
}

std::ostream&
operator << (std::ostream& os,
             const InfoDB::tRecord& r)
{
  os << "{";
  for (std::string::size_type i = 0; i < r.size(); ++i)
    os << (i == 0 ? "" : ", ") << r[i];
  os << "}";
   
  return os; 
}

std::ostream&
operator << (std::ostream& os,
             const InfoDB::tRecords& r)
{
  for (InfoDB::tRecords::size_type i = 0; i < r.size(); ++i) 
    os << i << " " << r[i] << std::endl;
   
  return os; 
}
