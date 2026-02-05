#include <HTML.H>
#include <HTTP.H>
#include <CGI.H>
#include <InfoDB.H>
#include <iostream>

namespace {
  std::string requiredVar(const CGI& cgi, 
                          const std::string& key, 
                          const std::string& msg) {
    const std::string value(cgi.get(key));

    if (value.empty()) {
      HTTP::errorPage(std::cout, 404, "No " + msg, 
                      "You did not enter a required field, " + msg + 
                      ", please go back and fill in the missing information.");
      exit(1);
    }

    return value;
  }
}

int 
main (int argc,
      char **argv)
{
  try {
    CGI cgi;

    const std::string hashValue(requiredVar(cgi, "hash", "hash"));
    const std::string key(requiredVar(cgi, "key", "key"));

    InfoDB info;

    if (info.authenticate(hashValue, key)) {
      HTTP::errorPage(std::cout, 200, "Authenticated", 
		      "Thank you for authenticating this change. It should appear"
		      " within the next 2 hours");
    } else {
    HTTP::errorPage(std::cout, 404, "Not Authenticated", 
		    "Updated authentication failed");
    }

    return 0;
  } catch (std::exception& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    throw;
  } catch (...) {
    std::cerr << argv[0] << " unknown caught an exception" << std::endl;
    HTTP::errorPage(std::cout, 404, "Exception", "Unknown Exception caught");
  }
  return 1;

}
