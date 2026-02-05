#include <InfoDB.H>
#include <DataDB.H>
#include <Tokenize.H>

int
main (int argc,
      char **argv)
{
  try {
    const DataDB::TYPE types[] = {DataDB::FLOW, DataDB::INFLOW, DataDB::OUTFLOW,
                                  DataDB::GAGE, DataDB::TEMPERATURE, DataDB::UNKNOWN};

    const std::string criteria("where time > now() - interval 10 day");
    DataDB data;
    InfoDB info;
    const InfoDB::tRecords& records(
	info.master("merged_dbs,db_name", "merged_dbs is not null and db_name is not null"));

    for (InfoDB::tRecords::const_iterator it = records.begin(); it != records.end(); ++it) {
      const InfoDB::tRecord& record(*it);
      const Tokenize mergedDBs(record[0]);
      const std::string& dbName(record[1]);
      for (int i = 0; types[i] != DataDB::UNKNOWN; ++i) {
        try {
          data.merge(dbName, mergedDBs, types[i], criteria);
        } catch (std::exception& e) {
          std::cerr << "Error merging into '" << dbName << "'" << std::endl
                    << "      mergedDBs " << mergedDBs << std::endl
                    << "      type " << types[i] << std::endl
                    << "      criteria '" << criteria << "'" << std::endl
                    << e.what() << std::endl; 
        }
      }
    }

    return 0;
  } catch (std::exception& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    throw;
  }
  return 1;
}
