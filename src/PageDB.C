#include <PageDB.H>
#include <Convert.H>
#include <String.H>

const std::string tableName("Pages");

bool
PageDB::operator () (const std::string& name,
                     const ACTION act,
                     const std::string& mimetype,
                     const int expires,
                     const std::string& page)
{
  if (!mDB.tableExists(tableName, true)) {
    mQuery << "create table if not exists " << tableName 
           << "(name varchar(128) key"
           << ",action enum('page','file','plot','view','edit','svg','png') not null"
           << ",expires int"
           << ",modified timestamp"
           << ",mimetype text"
           << ",page mediumtext);";

    mQuery.execute();

    mQuery << "insert into " << tableName << " values ('e', 'edit', 0, null, null, null);";
    mQuery.execute();
    mQuery << "insert into " << tableName << " values ('v', 'view', 0, null, null, null);";
    mQuery.execute();
    mQuery << "insert into " << tableName << " values ('f', 'plot', 0, null, 'flow', null);";
    mQuery.execute();
    mQuery << "insert into " << tableName << " values ('g', 'plot', 0, null, 'gage', null);";
    mQuery.execute();
    mQuery << "insert into " << tableName << " values ('t', 'plot', 0, null, 'temperature', null);";
    mQuery.execute();
    mQuery << "insert into " << tableName << " values ('svg', 'svg', 0, null, 'image/svg-xml', null);";
    mQuery.execute();
    mQuery << "insert into " << tableName << " values ('png', 'png', 0, null, 'image/png', null);";
    mQuery.execute();
  }

  mQuery << "replace into " << tableName << " values ("
         << mQuery.quote(name) 
         << ",'" << actionName(act) << "'"
         << "," << expires
         << ",CURRENT_TIMESTAMP" 
         << "," << mQuery.quote(mimetype)
         << "," << mQuery.quote(page)
         << ");";
  mQuery.execute();

  return mQuery;
}

PageDB::Page
PageDB::operator () (const std::string& name)
{
  if (!mDB.tableExists(tableName, false)) 
    return Page();

  mQuery << "select action,mimetype,unix_timestamp(modified),expires,page from " 
         << tableName 
         << " where name=" 
         << mQuery.quote(name) 
         << ";";

  MyQuery::Result result(mQuery.store());

  if (result.empty()) 
    return Page();

  MyQuery::Row row(result.at(0));

  const ACTION act(actionName(row.at(0).c_str()));
  const std::string mimeType(row.at(1).c_str());
  const time_t modified(row.at(2));
  const int expires(row.at(3));
  const std::string body(row.at(4).c_str());

  return Page(name, act, mimeType, 
              expires <= 0 ? (time(0) - expires) : (modified + expires), 
              modified, body);
}

std::string
PageDB::textQuery(const std::string& name,
                  const std::string& field)
{
  if (!mDB.tableExists(tableName, false)) 
    return std::string();

  mQuery << "select " << field << " from " << tableName 
         << " where name=" 
         << mQuery.quote(name) 
         << ";";

  MyQuery::Result result(mQuery.store());

  return result.empty() ? std::string() : result.at(0).at(0).c_str();
}

time_t
PageDB::modified(const std::string& name)
{
  if (!mDB.tableExists(tableName, false)) 
    return -1;

  mQuery << "select unix_timestamp(modified) from " << tableName 
         << " where name=" 
         << mQuery.quote(name) 
         << ";";

  MyQuery::Result result(mQuery.store());

  return result.empty() ? ((time_t) -1) : result.at(0).at(0);
}

time_t
PageDB::expires(const std::string& name)
{
  if (!mDB.tableExists(tableName, false)) 
    return -1;

  mQuery << "select expires,unix_timestamp(modified) from " << tableName 
         << " where name=" 
         << mQuery.quote(name) 
         << ";";

  MyQuery::Result result(mQuery.store());

  const int expires(result.at(0).at(0));

  return (expires <= 0) ? 
         (time(0) - expires) : 
         ((time_t) result.at(0).at(1) + expires);
}

const char *
PageDB::actionName(const ACTION action) 
{
  switch (action) {
  case PAGE: return "page";
  case FILE: return "file";
  case PLOT: return "plot";
  case EDIT: return "edit";
  case VIEW: return "view";
  case SVG:  return "svg";
  case PNG:  return "png";
  case UNKNOWN: return "unknown";
  }
  return "unknown";
}

PageDB::ACTION
PageDB::actionName(std::string action)
{
  String::toLowerInPlace(action); 

  if (action == "page") return PAGE;
  if (action == "file") return FILE;
  if (action == "plot") return PLOT;
  if (action == "view") return VIEW;
  if (action == "edit") return EDIT;
  if (action == "svg" ) return SVG;
  if (action == "png" ) return PNG;
  return UNKNOWN;
}
