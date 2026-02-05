#include <DataDB.H>
#include <Convert.H>

namespace {
  const time_t maxAge(2 * 366 * 24 * 60 * 60); // Keep 2 years worth
}

static bool
deleteRow(DataDB& data,
          const std::string& name,
          const time_t& when)
{
  return data.deleteRows(name, "unix_timestamp(time) = " + Convert::toStr(when));
}

static void
sparsifyTable(DataDB& data,
              const std::string& name)
{
  { // Deal with old age
    const time_t cutoff(time(0) - maxAge); // Toss out anything older than this

    data.deleteRows(name, "unix_timestamp(time) < " + Convert::toStr(cutoff));
  }

  { // delete duplicate values within a 24 hour window
    const DataDB::tRecords& records(data.records(name, std::string()));

    if (records.empty()) { // Empty, so drop the table
      data.dropTable(name);
      return;
    }

    DataDB::tRecords::size_type lastNotDropped(0);

    for (DataDB::tRecords::size_type i = 1; i < records.size() - 1; ++i) {
      if ((records[lastNotDropped].value == records[i].value) && 
          (records[i].value == records[i+1].value) &&
          (records[i+1].when >= (records[lastNotDropped].when - (24 * 60 * 60)))) {
        deleteRow(data, name, records[i].when);
      } else 
        lastNotDropped = i;
    }
  }

  // Sparsify data, most recent 10 days, keep all data, next 20 once/hour, 
  // next 30 6/day, and before that, 2/day
  {
    const DataDB::tRecords& records(data.records(name, std::string()));

    if (records.empty()) { // Empty, so drop the table
      std::cout << "Dropping " << name << std::endl;
      data.dropTable(name);
      return;
    }

    const time_t now(time(0));
    const time_t cut1(now - 10 * 24 * 60 * 60); // 10 days back
    const time_t cut2(now - 30 * 24 * 60 * 60); // 30 days back
    const time_t cut3(now - 60 * 24 * 60 * 60); // 30 days back

    DataDB::tRecords::size_type lastNotDropped(0);

    for (DataDB::tRecords::size_type i = 1; i < records.size() - 1; ++i) {
      if (records[i].when > cut1) {
        lastNotDropped = i;
      } else if (records[i].when > cut2) {
        if (records[i].when > (records[lastNotDropped].when  - (60 * 60)))
          deleteRow(data, name, records[i].when);
        else
          lastNotDropped = i;
      } else if (records[i].when > cut3) {
        if (records[i].when > (records[lastNotDropped].when  - (4 * 60 * 60)))
          deleteRow(data, name, records[i].when);
        else
          lastNotDropped = i;
      } else {
        if (records[i].when > (records[lastNotDropped].when  - (12 * 60 * 60)))
          deleteRow(data, name, records[i].when);
        else
          lastNotDropped = i;
      }
    }
  }
}

int
main (int argc,
      char **argv)
{
  try {
    DataDB data;
    const MyDB::tTables& tables(data.tables());

    for (MyDB::tTables::const_iterator it = tables.begin(); it != tables.end(); ++it)  {
      if ((*it != "Latest") && (*it != "url2name")) {
        try {
          sparsifyTable(data, *it);
        } catch (std::exception& e) {
          std::cerr << "Error sparsifying table '" << *it << "', " << e.what() << std::endl;
        }
      }
    }

    return 0;
  } catch (std::exception& e) {
    std::cerr << "Exception: " << e.what() << std::endl;
    return 1;
  }
}
