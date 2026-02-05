#include <InfoDB.H>
#include <DataDB.H>
#include <Tokenize.H>
#include <unistd.h>

std::string
tableName(const std::string& expr,
          InfoDB& info,
          DataDB& data)
{
  const std::string delim("::");
  const std::string::size_type hashIndex(expr.find(delim));
  const std::string::size_type typeIndex(expr.rfind(delim));

  if (hashIndex == expr.npos || (hashIndex == typeIndex)) {
    std::cerr << "Invalid expression format, '" << expr << "'" << std::endl;
    exit(1);
  }
 
  const std::string hash(expr.substr(0, hashIndex));
  const std::string type(expr.substr(typeIndex + delim.size()));
  const DataDB::TYPE typeCode(data.type(type));
  if ((typeCode == DataDB::ALL) || (typeCode == DataDB::UNKNOWN)) {
    std::cerr << "Invalid expression type(" << type << ") in '" << expr << "'" << std::endl;
    exit(1);
  }

  const InfoDB::tRecords& records(info.master("db_name", 
	"db_name is not null and hashValue = '" + hash + "'"));

  if (records.size() == 0) {
    std::cout << "No database name found for hash(" << hash << ") in expr(" << expr << ")"
              << std::endl;
    exit(1);
  }

  const std::string& dbName(records[records.size() - 1][0]);
  const std::string tableName(data.tableName(dbName, type));

  return tableName;
}

int
main (int argc,
      char **argv)
{
  bool qVerbose(false);

  for (int c; (c = getopt(argc, argv, "v")) != EOF;) {
    switch (c) {
    case 'v': qVerbose = true; break;
    }
  }

  try {
    const std::string latestName("Latest");
    DataDB data;
    InfoDB info;
    InfoDB info1; // For tableName lookup
    const InfoDB::tRecords& records(info.master("calc_time,calc_expr,calc_type,db_name",
             "calc_time is not null and calc_time != '' and "
             "calc_expr is not null and calc_expr != '' and "
             "calc_type is not null and calc_type != '' and "
             "db_name is not null and db_name != ''"));

    for (InfoDB::tRecords::const_iterator et(records.end()), it(records.begin()); it != et; ++it) {
      const InfoDB::tRecord& record(*it);
      if (qVerbose) {
        std::cout << "Working on " << record << std::endl;
      }
      try { 
        const Tokenize time(record[0]);

        if (time.empty()) 
          throw std::logic_error("Empty calc_time field for '" + record[1] + "'");

        std::string expr(record[1]);
        const std::string& type(record[2]);
        const std::string& dbName(record[3]);
        std::string timeExpr;
        std::string timeDelim;
        bool qBreak(false);
 
        for (Tokenize::const_iterator tte(time.end()), tt(time.begin()); tt != tte; ++tt) {
          const std::string& refName(*tt);
          const std::string name(tableName(refName, info1, data));
          if (!data.tableExists(name)) {
            std::cerr << "Table '" << name << "' does not exist!\n" << record << std::endl;
            qBreak = true;
            break;
          }
          const std::string select("(select value from " + latestName + " where name='" + name + "')");
          timeExpr += timeDelim + "(select time from " + latestName + " where name='" + name + "')";
          timeDelim = ", ";

          for(std::string::size_type index, pos = 0; 
              (index = expr.find(refName, pos)) != expr.npos;) {
            expr.replace(index, refName.size(), select);
            pos = index + select.size();
          }
        }

        if (qBreak)
           continue;

        if (time.size() != 1)
          timeExpr = "least(" + timeExpr + ")";

        data.replace(dbName, data.type(type), timeExpr + "," + expr);
        data.updateLatest(dbName, data.type(type));
      } catch (std::exception& e) {
          std::cerr << "Error calculating for " << record << std::endl
                    << e.what() << std::endl;
      }
    }

    return 0;
  } catch (std::exception& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    throw e;
  } catch (...) {
    std::cerr << "Error: Unknown exception caught" << std::endl;
    throw;
  }
  return 1;
}
