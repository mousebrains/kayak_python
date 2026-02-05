#include <DataDB.H>
#include <File.H>
#include <afstream.H>
#include <cerrno>

int
main (int argc, 
      char **argv)
{
  std::string prefix;

  for (int c; (c = getopt(argc, argv, "d:h")) != EOF;) {
    switch (c) {
    case 'd': prefix = optarg; break;
    default: std::cerr << "Unrecognized option (" << ((char) c) << ")" << std::endl;
    case 'h': std::cerr << "Usage: " << argv[0] << " -{d:h}" << std::endl
                        << std::endl
                        << "-d dir directory to save data into" << std::endl
                        << "-h     display this message" << std::endl;
              break;
    }
  }

  if (prefix.empty()) {
    std::cerr << "No directory specified!" << std::endl;
    return 1;
  }

  DataDB data;
  const MyDB::tTables& tables(data.tables());

  for (MyDB::tTables::const_iterator it = tables.begin(); it != tables.end(); ++it) {
    const std::string& name(*it);
    const std::string::size_type index(name.find('_'));
    if (index != name.npos) {
      const std::string gage(name.substr(index + 1));
      const std::string type(name.substr(0, index));
      const std::string dir(prefix + "/" + gage);
      const std::string fn(dir + "/" + type);
      if (!File::makedir(dir)) {
        std::cerr << "Error making directory(" << dir << ")" << std::endl;
        continue;
      }
      oafstream os(fn);
 
      if (!os) {
        std::cerr << "Error opening (" << fn << "), " << strerror(errno) << std::endl;
        continue;
      }

      const DataDB::tRecords& records(data.records(name, ""));

      for (DataDB::tRecords::size_type i = records.size(); i > 0; --i)
        os << records[i - 1].when << ' ' << records[i - 1].value << std::endl;
    }
  }
  
  return 0;
} 
