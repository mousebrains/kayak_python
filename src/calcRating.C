#include <InfoDB.H>
#include <DataDB.H>
#include <Tokenize.H>
#include <cmath>
#include <unistd.h>

class RatingTable {
private:
  typedef DataDB::tRatingTable tMap;
  tMap mFeet2CFS;
  tMap mCFS2Feet;

  double doit(const std::string& valueStr, const tMap& table, const double rnd) const;
public:
  RatingTable(const std::string& dbName, DataDB& data);

  double feet2cfs(const std::string& feet) const {return doit(feet, mFeet2CFS, 1);}
  double cfs2feet(const std::string& cfs) const {return doit(cfs, mCFS2Feet, 0.1);}

  size_t size() const {return mFeet2CFS.size();}
};

RatingTable::RatingTable(const std::string& dbName,
		         DataDB& data)
{
  mFeet2CFS = data.getRatingTable(dbName);
  for (tMap::const_iterator it(mFeet2CFS.begin()), et(mFeet2CFS.end()); it != et; ++it)
    mCFS2Feet.insert(std::make_pair(it->second, it->first));
}

double
RatingTable::doit(const std::string& valueStr, 
		  const tMap& table,
		  const double rnd) const
{
  if (table.empty() || (table.size() == 1))
    return FP_NAN;

  const double value(strtod(valueStr.c_str(), 0));
  tMap::const_iterator lb(table.lower_bound(value));

  double x1, x2, y1, y2;

  if (lb == table.end()) { // To far, so use last two values
    tMap::const_reverse_iterator it(table.rbegin());
    x1 = it->first;
    y1 = it->second;
    ++it;
    x2 = it->first;
    y2 = it->second;
  } else {
    x1 = lb->first;
    y1 = lb->second;
    if (lb == table.begin()) {
      ++lb;
    } else {
      --lb;
    }
    x2 = lb->first;
    y2 = lb->second;
  }

  const double pred((double) ((int) ((y1 + (y2 - y1) / (x2 - x1) * (value - x1)) / rnd)) * rnd);
  
  return pred;
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
    DataDB data;
    InfoDB info;
    InfoDB info1; // For tableName lookup
    const InfoDB::tRecords& records(info.master("db_name, db_rating",
             "db_name is not null and db_name != '' and "
             "db_rating is not null and db_rating != ''"));

    for (InfoDB::tRecords::const_iterator et(records.end()), it(records.begin()); it != et; ++it) {
      const InfoDB::tRecord& record(*it);
      try { 
        const std::string dbName(record[0]);
        const std::string dbRating(record[1]);
	const RatingTable rt(dbRating, data);
        const DataDB::tRecords feet(data.records(dbName, DataDB::GAGE));	
        const DataDB::tRecords cfs(data.records(dbName, DataDB::FLOW));	

	bool qNewFeet(false);
	bool qNewCFS(false);

        if (qVerbose)
          std::cout << "Working on " << record <<  " which contains "
		    << rt.size() << " rating entries and " 
		    << feet.size() << " feet entries and " 
		    << cfs.size() << " cfs entries" << std::endl;
	if (feet.empty()) { // convert all the CFS to feet, no need to check overlap
	  for (DataDB::tRecords::size_type ci(0), ce(cfs.size()); ci < ce; ++ci) {
	    const DataDB::Datum& c(cfs[ci]);
	    if (data(dbName, c.when, DataDB::GAGE, rt.cfs2feet(c.value))) {
	      qNewFeet = true;
            } else {
	      std::cerr << "Error in " << dbName << " at "
		        << ctime(&c.when) << " cfs(" << c.value
			<< ") -> feet(" << rt.cfs2feet(c.value) << ")" 
			<< std::endl; 
	    }
	    if (qVerbose) 
	      std::cout << c.when << " gage " << c.value << " -> " 
		        << rt.cfs2feet(c.value) << std::endl;
	  }
        } else if (cfs.empty()) { // convert all feet to CFS, no need to check overlap
	  for (DataDB::tRecords::size_type fi(0), fe(feet.size()); fi < fe; ++fi) {
	    const DataDB::Datum& f(feet[fi]);
	    const double cfs(rt.feet2cfs(f.value));
	    if (cfs > 0) {
	      if (data(dbName, f.when, DataDB::FLOW, cfs)) {
	        qNewCFS = true;
              } else {
	        std::cerr << "Error in " << dbName << " at "
		          << ctime(&f.when) << " feet(" << f.value
			  << ") -> cfs(" << cfs << ")"
			  << std::endl; 
	      }
	    }
	    if (qVerbose) 
	      std::cout << f.when << " flow " << f.value << " -> " 
		        << rt.feet2cfs(f.value) << std::endl;
	  }
        } else {
	  for (DataDB::tRecords::size_type ci(0), ce(cfs.size()), fi(0), fe(feet.size()); 
	       fi < fe; ++fi) {
	    const DataDB::Datum& f(feet[fi]);
	    const time_t ft(f.when);
	    for (; (ci < ce) && (cfs[ci].when > ft); ++ci) {
	      const DataDB::Datum& c(cfs[ci]);
	      if (data(dbName, c.when, DataDB::GAGE, rt.cfs2feet(c.value))) {
	        qNewFeet = true;
	      } else {
	      std::cerr << "Error in " << dbName << " at "
		        << ctime(&c.when) << " cfs(" << c.value
			<< ") -> feet(" << rt.cfs2feet(c.value) << ")" 
			<< std::endl; 
              }
            }
	    if ((ci < ce) && (cfs[ci].when == ft)) {
	      ++ci;
	      continue;
	    }
	    const double cfs(rt.feet2cfs(f.value));
	    if (cfs > 0) {
	      if (data(dbName, ft, DataDB::FLOW, cfs)) {
	        qNewCFS = true;
              } else {
	        std::cerr << "Error in " << dbName << " at "
		          << ctime(&f.when) << " feet(" << f.value
			  << ") -> cfs(" << cfs << ")" 
			  << std::endl; 
	      }
	    }
	  }
	}
	if (qNewCFS) 
          data.updateLatest(dbName, DataDB::FLOW);
	if (qNewFeet) 
          data.updateLatest(dbName, DataDB::GAGE);
      } catch (std::exception& e) {
          std::cerr << "Error calculating for " << record << std::endl
                    << e.what() << std::endl;
      }
    }

    data.wrapup();

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
