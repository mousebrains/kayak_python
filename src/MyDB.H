#ifndef INC_MyDB_H_
#define INC_MyDB_H_

#include <mysql++/mysql++.h>
#include <cstdio>

class MyQuery {
public:
  typedef mysqlpp::Query Query;
  typedef mysqlpp::SimpleResult SimpleResult;
  typedef mysqlpp::StoreQueryResult Result;
  typedef mysqlpp::Row Row;
  typedef mysqlpp::String ColData;
private:
  Query mQuery;
public:
  MyQuery(Query query) : mQuery(query) {}

  operator bool() const {return bool(mQuery);}
  std::string str() {return mQuery.str();}
  SimpleResult execute();
  Result store();
  void reset() {mQuery.reset();}

  std::string quote(const std::string& a) const {
    std::string b(a); // Local copy
    mQuery.escape_string(&b); // Modify b
    return "'" + b + "'";
  }

  template<typename T>
  MyQuery& operator << (const T& sql) {
    mQuery << sql;
    return *this;
  }
}; // MyQuery

std::ostream& operator << (std::ostream& os, const MyQuery::Result& result);
std::ostream& operator << (std::ostream& os, const MyQuery::Row& row);

  
class MyDB {
public:
  typedef mysqlpp::Connection Connection;
  typedef std::set<std::string> tTables;
private:
  Connection mConnection;
  MyQuery mQuery;

  tTables mTables;

  const tTables& tablesInternal();
public:
  MyDB(const std::string& database);

  Connection& connection() {return mConnection;}
  MyQuery& query() {return mQuery;}

  bool tableExists(const std::string& name, const bool willCreate);

  const tTables& tables() {return mTables.empty() ? tablesInternal() : mTables;}

  bool dropTable(const std::string& name);
  bool deleteRows(const std::string& name, const std::string& criteria);
};

std::ostream& operator << (std::ostream& os, const MyDB::tTables& tables);

#endif // INC_MyDB_H_
