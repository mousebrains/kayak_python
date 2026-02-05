#ifndef INC_ParameterDB_H_
#define INC_ParameterDB_H_

#include <MyDB.H>

class ParameterDB {
private:
  MyDB mDB;
  MyQuery& mQuery;
public:
  ParameterDB() : mDB("levels_information"), mQuery(mDB.query()) {}

  std::string dirName(const std::string& dirKey);
  std::string fileName(const std::string& dirKey, const std::string& fileKey);

  std::string operator () (const std::string& key);
  bool operator () (const std::string& key, const std::string& value);
};

#endif // INC_ParameterDB_H_
