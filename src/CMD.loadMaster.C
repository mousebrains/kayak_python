#include <CMD.H>
#include <InfoDB.H>
#include <HTTP.H>

bool
CMD::loadMaster(const std::string& hash, 
		std::string& displayName, 
		std::string& dbName) 
{
  if (badHash(hash)) 
    return 1;

  InfoDB info;
  const InfoDB::tRecords& dbNames(info.master("db_name,display_name,gauge_location", 
                                              "HashValue='" + hash + "'"));

  if (dbNames.size() != 1) {
    if (dbNames.empty()) 
      HTTP::errorPage(std::cout, 404, "No database found", 
                      "No database found for (" + hash + ")");
    else
      HTTP::errorPage(std::cout, 404, "Too many databases found", 
                      "Too many databases found for (" + hash + ")");
    return false;
  }
 
  dbName = dbNames[0][0];
  const std::string& riverName(dbNames[0][1]);
  const std::string& gageLocation(dbNames[0][2]);
  displayName = riverName + (gageLocation.empty() ? "" : ("@" + gageLocation));
  return true;
}
