#include <Paths.H>
#include <HTML.H>
#include <HTTP.H>
#include <CGI.H>
#include <String.H>
#include <MakeDescription.H>
#include <iostream>

namespace {
  std::string requiredVar(const CGI& cgi, 
                          const std::string& key, 
                          const std::string& msg, 
                          const bool email) {
    const std::string value(cgi.get(key));

    if (value.empty()) {
      HTTP::errorPage(std::cout, 404, "No " + msg, 
                      "You did not enter a required field, " + msg + 
                      ", please go back and fill in the missing information.");
      exit(1);
    }

    if (email) {
      if (value.find('@') == value.npos) {
        HTTP::errorPage(std::cout, 404, "Invalid " + msg, 
                        "The " + msg + " you entered is not of the form username@host," +
                        ", please go back and correct in the information.");
        exit(1);
      }
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
    const std::string hash(requiredVar(cgi, "hash", "hash", false));
    const std::string userName(requiredVar(cgi, "userName", "user name", false));
    const std::string email(requiredVar(cgi, "email", "e-mail address", true));

    typedef std::map<std::string, std::string> tDiff;
    tDiff diff;

    for (CGI::const_iterator it = cgi.begin(); it != cgi.end(); ++it) {
      const std::string& key(it->first);
      const std::string& val(it->second);
      const std::string prefix("pre.");
      if (key.find(prefix) == 0) { // Found a pre existing field, so now check if it has changed
        const std::string var(key.substr(prefix.size()));
        const std::string prop(cgi.get(var));
        if (prop != val) 
          diff.insert(std::make_pair(String::toLower(var), prop));
      }
    }

    if (diff.empty()) {
      HTTP::errorPage(std::cout, 404, "No change", "You did not change anything.");
      return 1;
    }

    std::ostringstream os;
    MakeDescription mk(os, false, false, false);
    const std::string fields(mk.fields());
    InfoDB info;
    const InfoDB::tRecords& records(info.master(fields, "hashValue='" + hash + "'"));

    if (records.size() != 1) {
      if (records.empty())
        HTTP::errorPage(std::cout, 404, "No records found", "No records found.");
      else
        HTTP::errorPage(std::cout, 404, "Too many records found", "Too many records found.");
      return 1;
    }

    InfoDB::tRecord record(records[0]);
    Tokenize tokens(fields, " ,\n\t");

    for (Tokenize::size_type i = 0; i < tokens.size(); ++i) {
      tDiff::const_iterator it(diff.find(String::toLower(tokens[i])));
      if (it != diff.end())
        record[i] = it->second;
    }

    mk.spew(record);
    mk.close(hash);

    diff.insert(std::make_pair("username", userName));
    diff.insert(std::make_pair("email", email));

    if (cgi.get("update") == "New Run") {
      const InfoDB::tRecords& records(info.master("*", "hashValue='" + hash + "'"));
      const InfoDB::tRecord record(records[0]);
      const InfoDB::tRecords& columns(info.masterColumns());
      const std::string newHash(info.newHash());
      diff.insert(std::make_pair("hashvalue", newHash));

      for (InfoDB::tRecords::size_type i = 0; i < columns.size(); ++i) {
        const std::string key(String::toLower(columns[i][0]));
        if (diff.find(key) == diff.end())
          diff.insert(std::make_pair(key, record[i]));
      }
    } else {
      diff.insert(std::make_pair("hashvalue", hash));
    }
  
    HTML html(HTTP::compressable());

    html.head("Submit Approval");

    html << "Your updates will look like:" << std::endl;
    html << "<hr />" << std::endl;

    html << os.str() << std::endl;

    html << "<hr />" << std::endl;
    html << "If this is okay, please click approved below. "
	 << "If not, then click reject." << std::endl;
    html << "If you click approve, you will be sent an e-mail with a hyper link to"
	 << " to authenticate your e-mail address." << std::endl;
    html << "<form method=\"post\" action=\"" << Paths::CGIRoot 
	 << "approve\">" << std::endl;
    html << "<input type=\"submit\" name=\"approved\" value=\"Approve\">" << std::endl;
    html << "<input type=\"submit\" name=\"rejected\" value=\"Reject\">" << std::endl;

    for (tDiff::const_iterator it = diff.begin(); it != diff.end(); ++it)
      html << "<input type=\"hidden\" name=\"var." << it->first << "\" value=\"" << it->second 
           << "\">" << std::endl;
    
    html << "</form>" << std::endl;

    HTTP http(std::cout, &html);
    http.content();
    http.modified(time(0));
    http.expires(time(0));

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
