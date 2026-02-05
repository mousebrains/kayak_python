#include <HTML.H>
#include <HTTP.H>
#include <CGI.H>
#include <InfoDB.H>
#include <pstream.H>
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

  std::string mkKey() {
    const std::string a("abcdefghijklmnopqrstuvxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789");
    const int nChars(64);
    srand48(time(0));
    std::string key;
    for (int i = 0; i < nChars; ++i) {
      key += a.substr((int)(a.size() * drand48()), 1);
    }
    return key;
  }
}

int 
main (int argc,
      char **argv)
{
  try {
    CGI cgi;

    if (cgi.isSet("rejected")) {
      HTTP::errorPage(std::cout, 200, "Updates tossed", "Updates tossed");
      return 0;
    }

    std::string hashValue(requiredVar(cgi, "var.hashvalue", "hash"));
    const std::string userName(requiredVar(cgi, "var.username", "user name"));
    const std::string email(requiredVar(cgi, "var.email", "e-mail address"));

    InfoDB::tCorrections vars;

    for (CGI::const_iterator et(cgi.end()), it(cgi.begin()); it != et; ++it) {
      const std::string& key(it->first);
      if ((key != "var.username") && (key != "var.email") && (key != "var.hashvalue")) {
        const std::string& val(it->second);
        const std::string prefix("var.");
        if (key.find(prefix) == 0) { // Found a variable existing field, 
          const std::string var(key.substr(prefix.size()));
          vars.insert(std::make_pair(var, val));
        }
      }
    }

    if (vars.empty()) {
      HTTP::errorPage(std::cout, 404, "No Variables", "You did not change anything.");
      return 1;
    }

    InfoDB info;

    const std::string randomKey(mkKey());
    if (info.corrections(hashValue, userName, email, vars, randomKey)) {
      {
	std::vector<std::string> args;
	args.push_back("-s");
	args.push_back("River Description Authenticaton");
	args.push_back(email);
        opstream os("/usr/bin/mailx", args);
        os << "You, or somebody claiming to be you changed a river description.\n"
	   << "If it was you, and you wish to authenticate the change, please visit\n"
	   << Paths::URL << Paths::CGIRoot 
	   << "authenticate?hash=" << hashValue
	   << "&key=" << randomKey << "\n"
	   << "within the next 24 hours. If you do not wish to approve this, please\n"
	   << "do nothing.\n"
	   << "\n"
	   << "Thank you,\n"
	   << Paths::MaintainerName << "\n"
	   ;
      }
      HTTP::errorPage(std::cout, 200, "Submited", 
		      "Submitted for authentication, you have been sent an "
		      "e-mail with a link you must visit within 24 hours to "
		      "authorize this correction.");
      return 0;
    }

    HTTP::errorPage(std::cout, 404, "Updated Failed", "Update failed");

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
