#include <InfoDB.H>
#include <File.H>
#include <fstream>
#include <cerrno>
#include <unistd.h>

namespace {
  void dumpDB(InfoDB& info, const std::string& name, std::ostream& os, const std::string& comment)
  {
    MyQuery& query(info.query());
    std::vector<std::string> names;
  
    {
      query << "show full columns from " << name << ";";
      MyQuery::Result desc(query.store());
  
      os << "use levels_information;" << std::endl;
      os << "drop table if exists " << name << ";" << std::endl;
      os << "create table " << name << " (" << std::endl;
  
      std::string comma("  ");
  
      for (MyQuery::Result::const_iterator it(desc.begin()), et(desc.end()); it != et; ++it) {
        const MyQuery::Row& row(*it);
        const std::string name(row.at(0).c_str());
        const std::string fieldType(row.at(1).c_str());
        const std::string key(row.at(4).c_str());
        const std::string comment(row.at(8).c_str());
        names.push_back(name);
        os << comma << name << " " << fieldType;
        if (!key.empty())
          os << " key ";
        if (!comment.empty()) 
          os << " comment " << query.quote(comment);
        comma = ",\n  ";
      }
  
      os << "\n  )" << std::endl;
      os << " comment " << query.quote(comment) << ";" << std::endl << std::endl;
    }
    {
      query << "select * from " << name << " order by Sort_Key;";
      MyQuery::Result desc(query.store());
      for (MyQuery::Result::const_iterator it(desc.begin()), et(desc.end()); it != et; ++it) {
        const MyQuery::Row& row(*it);
        os << "insert into " << name << " set " << std::endl;
	std::string comma("  ");
        for (MyQuery::Row::size_type i(0), e(row.size()); i < e; ++i) {
  	  const MyQuery::ColData& cell(row.at(i));
  	  if (!cell.is_null()) {
  	    os << comma << names[i] << "=" << query.quote(cell.c_str());
	    comma = ",\n  ";
          }
        }
        os << "\n;\n\n";
      }
    }
  }
}

int
main (int argc, 
      char **argv)
{
  std::string filename;

  for (int c; (c = getopt(argc, argv, "ho:")) != EOF;) {
    switch (c) {
    case 'o': filename = optarg; break;
    default: std::cerr << "Unrecognized option (" << ((char) c) << ")" << std::endl;
    case 'h': std::cerr << "Usage: " << argv[0] << " -{d:h}" << std::endl
                        << std::endl
                        << "-o fn filename to save data into" << std::endl
                        << "-h    display this message" << std::endl;
              break;
    }
  }

  const std::string master("Master");
  const std::string corrections("Corrections");

  InfoDB info;
  std::ostream *os(0);
  if (!filename.empty()) {
    os = new std::ofstream(filename.c_str());
    if (!os) {
      std::cerr << "Unable to create ofstream for '" << filename << "'" << std::endl;
      return 1;
    }
    if (!(*os)) {
      std::cerr << "Unable to create ofstream for '" << filename << "', " << strerror(errno) << std::endl;
      return 1;
    }
  } 

  dumpDB(info, "Master", os ? *os : std::cout, "Master river information");
  dumpDB(info, "Corrections", os ? *os : std::cout, "Corrections records for master table");
 
  if (os)
    delete os;

  return 0;
} 
