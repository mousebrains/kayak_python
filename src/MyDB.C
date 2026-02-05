#include <MyDB.H>
#include <Paths.H>

MyQuery::SimpleResult
MyQuery::execute() 
{
  try {
    return mQuery.execute();
  } catch (std::exception& e) {
    std::cerr << "Error executing SQL" << std::endl;
    std::cerr << mQuery.str() << std::endl;
    std::cerr << mQuery.error() << std::endl;
    std::cerr << e.what() << std::endl;
    throw e;
  }
}

MyQuery::Result
MyQuery::store() 
{
  try {
    return mQuery.store();
  } catch (std::exception& e) {
    std::cerr << "Error executing SQL" << std::endl;
    std::cerr << mQuery.str() << std::endl;
    std::cerr << mQuery.error() << std::endl;
    std::cerr << e.what() << std::endl;
    throw e;
  }
}

MyDB::MyDB(const std::string& database)
  : mConnection(database.c_str(), 
                Paths::MySQLHost.c_str(), 
                Paths::MySQLUser.c_str(), 
                Paths::MySQLPasswd.c_str()),
    mQuery(MyQuery(mConnection.query()))
{
}

const MyDB::tTables&
MyDB::tablesInternal()
{
  mTables.clear();

  mQuery << "show tables;";

  const MyQuery::Result result(mQuery.store());

  for (MyQuery::Result::size_type i = 0; i < result.size(); ++i) 
    mTables.insert(result.at(i).at(0).c_str());
  
  return mTables;
}

bool
MyDB::tableExists(const std::string& name,
                  const bool willCreate)
{
  if (mTables.empty())
    tablesInternal();

  const bool rc(mTables.find(name) != mTables.end());

  if (!rc && willCreate)
    mTables.insert(name);

  return rc;
}

bool
MyDB::dropTable(const std::string& tableName)
{
  mQuery << "drop table if exists " << tableName << ";";
  mQuery.execute();
  return mQuery;
}

bool
MyDB::deleteRows(const std::string& tableName,
                 const std::string& criteria)
{
  if (criteria.empty())
    throw "Empty criteria found for " + tableName;

  mQuery << "delete from " << tableName << " where " << criteria << ";";
  mQuery.execute();
  return mQuery;
}

std::ostream&
operator << (std::ostream& os,
             const MyQuery::Result& res)
{
  for (MyQuery::Result::size_type i = 0; i < res.size(); ++i) 
    os << "Result[" << i << "] = {" << res.at(i) << "}" << std::endl;

  return os;
}

std::ostream&
operator << (std::ostream& os,
             const MyQuery::Row& row)
{
  std::string delim;

  for (MyQuery::Row::size_type i = 0; i < row.size(); ++i) {
    os << delim << row.at(i);
    delim = ", ";
  }

  return os;
}

std::ostream&
operator << (std::ostream& os,
             const MyDB::tTables& tables)
{
  std::string delim;

  for (MyDB::tTables::const_iterator it = tables.begin(); it != tables.end(); ++it) {
    os << delim << *it;
    delim = ", ";
  }

  return os;
}
