#include <DataDB.H>
#include <File.H>
#include <Directory.H>
#include <fstream>
#include <cerrno>
#include <map>

std::string mkGage(std::string name) {
  const std::string delim(".-+");

  for (std::string::size_type i; (i = name.find_first_of(delim)) != name.npos;) {
    const std::string::size_type e(name.find_first_not_of(delim, i));
    if (e == name.npos)
      name = name.substr(0, i);
    else
      name = name.replace(i, e - i, "_");
  }
  return name;
}

int
main (int argc,
      char **argv)
{
  typedef std::map<std::string, DataDB::TYPE> tTypes;
  tTypes types;
  types.insert(std::make_pair("gauge", DataDB::GAGE));
  types.insert(std::make_pair("flow", DataDB::FLOW));
  types.insert(std::make_pair("temperature", DataDB::TEMPERATURE));
  types.insert(std::make_pair("inflow", DataDB::INFLOW));
  types.insert(std::make_pair("outflow", DataDB::OUTFLOW));

  try {
    const time_t now(time(0));
    DataDB db;

    for (int i = 1; i < argc; ++i) {
      const std::string gage(mkGage(File::tail(argv[i])));
      const std::string dirName(argv[i]);
      Directory dir(dirName);
      for (std::string fn; !(fn = dir.read()).empty();) {
        tTypes::const_iterator it(types.find(fn));
        if (it != types.end()) {
	  const DataDB::TYPE type(it->second);
	  const bool zeroCheck(type != DataDB::GAGE);
          const std::string filename(dirName + "/" + fn);
	  std::ifstream is(filename.c_str());
	  if (!is) {
	    std::cerr << "Error opening " << filename << ", " << strerror(errno) << std::endl;
	    continue;
	  }

	  typedef std::map<time_t, double> tData;
	  tData data;
	  for (std::string line; getline(is, line);) {
            time_t t;
	    double value;
	    std::istringstream iss(line);
	    if ((iss >> t >> value) && (t < now) && (!zeroCheck || (value >= 0))) {
	      tData::iterator dt(data.find(t));
	      if (dt == data.end())
                data.insert(std::make_pair(t, value));
	      else
	        dt->second = value;
            }
	  }
	  if (!data.empty()) {
            std::cout << i << " " << gage << " " << fn << " " << data.size() << std::endl;
	    for (tData::const_iterator it(data.begin()), et(data.end()); it != et; ++it) {
	      db(gage, it->first, type, it->second);
	    }
            db.wrapup();
	  }
        }
      }
    }

  } catch (std::exception& e) {
    std::cerr << "Exception caught, " << e.what() << std::endl;
  }
}
