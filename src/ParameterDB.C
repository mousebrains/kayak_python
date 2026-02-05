#include <ParameterDB.H>

namespace {
  const std::string tableName("Parameters");
  const std::string keyName("ident");
  const std::string valueName("value");
}

std::string
ParameterDB::operator () (const std::string& key)
{
  mQuery << "select " << valueName << " from " << tableName
         << " where " << keyName << "=" << mQuery.quote(key) << ";";

  const MyQuery::Result result(mQuery.store());

  if (result.size() == 1) {
    MyQuery::ColData value(result.at(0).at(0));
    return value.is_null() ? "" : value.c_str();
  }

  std::cerr << "ERROR: Failed to get a single result for parameter(" << key
            << ") " << result.size() << std::endl;
  std::cerr << result;

  throw "Failed to get a single result for parameter(" + key + ")";
}

bool
ParameterDB::operator () (const std::string& key,
                          const std::string& value)
{
  mQuery << "insert into " << tableName  << " values ("
         << mQuery.quote(key) << ","
         << mQuery.quote(value)
         << ") on duplicate key update " << valueName << "=values(" << valueName << ");";

  mQuery.execute();

  return mQuery;
}

std::string
ParameterDB::dirName(const std::string& dirKey)
{
  const std::string dir((*this)(dirKey));

  if (dir.substr(0,1) == "/")
    return dir;

  const std::string rootDir((*this)("rootDir"));

  return rootDir.empty() ? dir : (rootDir + "/" + dir);
}

std::string
ParameterDB::fileName(const std::string& dirKey,
                      const std::string& fileKey)
{
  const std::string dir(dirName(dirKey));
  const std::string fn((*this)(fileKey));

  if (fn.empty()) 
    throw "Empty filename for parameter '" + fileKey + "' in ParameterDB::fileName";

  return dir.empty() ? fn : (dir + "/" + fn);
}
