#include <Parse_IdahoPower.H>
#include <HTMLrender.H>
#include <Curl.H>
#include <iostream>

namespace Parsers {
  IdahoPower::IdahoPower(const Curl& curl,
             const bool qVerbose,
             const bool qDryRun,
	     DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db)
  {
    // try {
      // HTMLrender xml(curl.str(), mURL);
      // for (XML::const_iterator it = xml.begin(); it != xml.end(); ++it) {
        // std::cout << "path(" << it->path() << ")" << std::endl;
      // }
    // } catch (const std::exception& e) {
      // std::cout << "Caught exception in IdahoPower, " << e.what() << std::endl;
    // } catch (...) {
      // std::cout << "Caught unknown exception in IdahoPower" << std::endl;
    // }
  
    serveUpCookedLines(curl.str()); 
  }

  bool
  IdahoPower::line(const std::string& l)
  {
    if (mDebug)
      std::cout << l << std::endl;

    return true;
  }
}
