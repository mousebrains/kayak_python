#include <ReadFile.H>
#include <String.H>
#include <ParameterDB.H>
#include <InfoDB.H>
#include <PageArchive.H>
#include <iostream>
#include <sstream>
#include <set>
#include <cerrno>
#include <string>
#include <unistd.h>

namespace {
  std::string dumpRow(const std::string& state, const std::string& label) {
    return ("<tr align=\"left\"><th><a href=\"?P=" + state + ".html\">" + label +
            "</a>&nbsp;</th><td><a href=\"?P=" + state + ".text\">Text version</a></td></tr>");
  }
}

int
main (int argc,
      char **argv)
{
  const char *options("fh");

  bool forceFlag(false);

  for (int c; (c = getopt(argc, argv, options)) != EOF;) {
    switch (c) {
      case 'f': forceFlag = true; break;
      default: std::cerr << "Unrecognized option(" << ((char) c) << ")" << std::endl;
      case 'h': 
        std::cerr << "Usage: " << argv[0] << "-{" << options << "}" << std::endl;
        std::cerr << std::endl;
        std::cerr << "-f force page generation"<< std::endl;
        std::cerr << "-h display this message"<< std::endl;
        exit(1);
    }
  }

  try {
    InfoDB info;
    ParameterDB params;
    const std::string name("main");
    const std::string mimetype("text/html");
    const time_t modified(PageArchive::lastModified(name));
 
    if (!forceFlag && !modified && (info.lastUpdate() < (modified - 120)))
      return 0;
  
    const std::string header(params.fileName("templateDir", "web.pre.filename"));
    const std::string tail(params.fileName("templateDir", "web.post.filename"));
  
    const InfoDB::tStates& states(info.allStates());
  
    if (states.empty()) {
      std::cerr << "ERROR: No states found" << std::endl;
      return 1;
    }
 
    std::ostringstream os;
  
    if (!header.empty()) 
      os << ReadFile(header, false); 
  
    os << "<table cellspacing=\"1\" cellpadding=\"0\">" << std::endl;
    os << dumpRow("All", "All states") << std::endl;
  
    for (InfoDB::tStates::const_iterator it = states.begin(); it != states.end(); ++it) {
      os << dumpRow(*it, String::replace(*it, "_", " ")) << std::endl;
    }
  
    os << "</table>" << std::endl;

    if (!tail.empty())
      os << ReadFile(tail, false); 

    PageArchive::dumpPage(name, mimetype, 24 * 60 * 60, os.str());
    return 0;
  } catch (std::exception& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    throw;
  }
  return 1;
}
