#include <MakeDescription.H>
#include <PageArchive.H>
#include <unistd.h>

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
    const std::string tableName("d");
    const time_t modified(PageArchive::lastModified(tableName));
 
    if (!forceFlag && !modified && (info.lastUpdate() < (modified - 120)))
      return 0;

    std::ostringstream os;
    MakeDescription mk(os, true, true, false);
    mk.master("", "no_show is null");
    mk.close(std::string());
 
    PageArchive::dumpPage(tableName, "text/html", 24 * 60 * 60, os.str());
    return 0;
  } catch (std::exception& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    throw;
  }
  return 1;
}
