#include <InfoDB.H>
#include <DataDB.H>
#include <Tokenize.H>
#include <map>
#include <set>

typedef std::set<std::string> tKnown;

void addKnown(const std::string& str, tKnown& k)
{
  const Tokenize tokens(str, " \t\n,");

  for (Tokenize::const_iterator it(tokens.begin()), et(tokens.end()); 
       it != et; ++it) 
    k.insert(*it);
}

int
main (int,
      char **)
{
  tKnown known;
  {
    InfoDB info;

    const InfoDB::tRecords iRec(info.master("db_name,merged_dbs"));

    for (InfoDB::tRecords::const_iterator it(iRec.begin()), et(iRec.end()); 
          it != et; ++it) {
      const InfoDB::tRecord rec(*it);
      addKnown(rec[0], known);
      addKnown(rec[1], known);
    }
  }

  DataDB data;
  const DataDB::tNamesURLs names(data.namesAndURLs());

  for (DataDB::tNamesURLs::const_iterator it(names.begin()), et(names.end());
       it != et; ++it) {
    if (known.find(it->first) == known.end())
      std::cout << it->first << " " << it->second << std::endl;
  }

  return 0;
}
