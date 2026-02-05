#include <Paths.H>
#include <HTML.H>
#include <HTTP.H>
#include <CGI.H>
#include <String.H>
#include <InfoDB.H>
#include <iostream>

namespace {
  bool spew(HTML& html) {
    HTTP http(std::cout, &html);
    http.content();
    http.modified(time(0));
    http.expires(time(0));
    return true;
  }

  bool doStates() {
    InfoDB info;
    const InfoDB::tStates states(info.allStates());

    HTML html(HTTP::compressable());

    html.head("Choose states");
  
    html << "<h1>Pick which states you want to select rivers from</h1>" << std::endl;
    html << "<form"
         << " method=\"get\""
         << " action=\"" << Paths::CGIRoot << "picker\""
         << ">" << std::endl; 
    html << "<div><input type=\"hidden\" name=\"states\" value=\"" << states << "\">" 
         << "</input></div>" << std::endl;

    for (InfoDB::tStates::const_iterator et(states.end()), it(states.begin()); it != et; ++it) 
      html << "<div><input type=\"checkbox\" name=\"state." << *it 
           << "\" value=\"1\"></input>" << String::replace(*it, "_", " ") 
           << "</div>" << std::endl;

    html << "<div><input type=\"submit\" value=\"Select Rivers\">" 
         << "</input></div>" << std::endl;
    html << "</form>" << std::endl; 

    return spew(html);
  }

  std::string displayName(const std::string& name, 
                          const std::string& gage, 
                          const std::string& state)
  {
    return name + 
           (gage.empty() ? "" : "@") + gage + 
           (state.empty() ? "" : ("(" + String::replace(state, "_", " ") + ")"));
  }

  bool selectRivers(const CGI& cgi) {
    const Tokenize states(cgi.get("states"), " ,\n\t");
    std::string criteria;

    for (Tokenize::const_iterator et(states.end()), it(states.begin()); it != et; ++it) 
      if (cgi.isSet("state." + *it))
        criteria += (criteria.empty() ? std::string() : std::string(" or ")) +
                    "state like '%" + *it + "%'";

    if (criteria.empty()) 
      return false;

    criteria = "display_name is not null and gauge_location is not null and no_show is null and ("
             + criteria + ")";

    InfoDB info;
    const InfoDB::tRecords& records(info.master("hashValue,display_name,gauge_location,state",
                                                criteria));

    HTML html(HTTP::compressable());

    html.head("Choose Rivers");

    html << "<h1>Select the rivers you are interested in building a page for</h1>" << std::endl; 
    html << "<h2>Please note only the first 20 will be kept</h2>" << std::endl;
    html << "<form"
         << " method=\"get\""
         << " action=\"" << Paths::CGIRoot << "picker\""
         << ">" << std::endl; 

    std::string hashes;

    for (InfoDB::tRecords::size_type i = 0; i < records.size(); ++i) {
      const std::string& hash(records[i][0]);
      html << "<div><input type=\"checkbox\" value=\"1\" name=\"" << hash << "\">" 
           << "</input>"
           << displayName(records[i][1], records[i][2], records[i][3]) 
           << "</div>" << std::endl;
      hashes += (hashes.empty() ? "" : ",") + hash;
    }

    html << "<div>" << std::endl
         << "<input type=\"submit\" value=\"Order Rivers\">" 
         << "</input>" << std::endl
         << "<input type=\"hidden\" value=\"" << hashes << "\" name=\"hashes\">" 
         << "</input>" << std::endl
         << "<input type=\"hidden\" value=\"" << states << "\" name=\"states\">" 
         << "</input>" << std::endl
         << "</div>" << std::endl
         << "</form>" << std::endl; 

    return spew(html);
  }

  bool orderRivers(const CGI& cgi) {
    const int maxEntries(20);
    int active(0);
    Tokenize hashes(cgi.get("hashes"), " ,\t\n");
    std::string criteria;

    for (Tokenize::const_iterator et(hashes.end()), it(hashes.begin()); it != et; ++it) {
      if (cgi.isSet(*it)) {
        criteria += (criteria.empty() ? "" : ",") + *it;
        if (++active >= maxEntries)
          break;
      }
    }

    if (criteria.empty()) 
      return false;

    criteria = "find_in_set(hashValue, '" + criteria + "')";
    
    InfoDB info;
    const InfoDB::tRecords& records(info.master("hashValue,display_name,gauge_location,state",
                                                criteria));
    if (records.empty())
      return false;

    typedef std::map<std::string, std::string> tMap;
    tMap m;
    for (InfoDB::tRecords::size_type i = 0; i < records.size(); ++i)
      m.insert(std::make_pair(records[i][0], 
                              displayName(records[i][1], records[i][2], records[i][3])));

    for (Tokenize::size_type i = 0; i < hashes.size(); ++i) {
      const std::string& hash(hashes[i]);
      if (cgi.isSet("up." + hash)) {
        if (i != 0) {
          const std::string tmp(hash);
          hashes[i] = hashes[i-1];
          hashes[i-1] = tmp;
          break;
        }
      } else if (cgi.isSet("down." + hash)) {
        if (i != (hashes.size() - 1)) {
          const std::string tmp(hash);
          hashes[i] = hashes[i+1];
          hashes[i+1] = tmp;
          break;
        }
      }
    }

    HTML html(HTTP::compressable());

    html.head("Order Rivers");
    html << "<h1>Order rivers by using the up/down buttons</h1>" << std::endl;
    html << "<form"
         << " method=\"get\""
         << " action=\"" << Paths::CGIRoot << "picker\""
         << ">" << std::endl; 

    std::string newHashes;
    for (Tokenize::const_iterator et(hashes.end()), it(hashes.begin()); it != et; ++it) {
      tMap::const_iterator mt(m.find(*it));
      if (mt != m.end()) {
        newHashes += (newHashes.empty() ? "" : ",") + *it;
        html << "<div>"
             << "<input type=\"submit\" value=\"Up\" name=\"up." << *it << "\">" 
             << "</input>"
             << "<input type=\"submit\" value=\"Down\" name=\"down." << *it << "\">" 
             << "</input>"
             << "<input type=\"hidden\" value=\"1\" name=\"" << *it << "\">"
             << "</input>"
             << mt->second 
             << "</div>"
             << std::endl;
      }
    }

    html << "<div>"
         << "<input type=\"hidden\" value=\"" << newHashes << "\" name=\"hashes\">" 
         << "</input></div>" << std::endl;
    html << "</form>" << std::endl; 
    html << "<div>Click the following link to display your page. If you like it, please"
         << " bookmark it, and then you can come back to it whenever you want." 
         << "</div>" << std::endl;
    html << "<div><a href=\"" << Paths::CGIRoot << "makePage?h=" 
	 << newHashes << "\">Generate Page</a></div>" << std::endl;

    return spew(html);
  }
}

int 
main (int argc,
      char **argv)
{
  try {
    const CGI cgi;

    if (orderRivers(cgi)) return 0;
    if (selectRivers(cgi)) return 0;
    if (doStates()) return 0;
    return 1;
  } catch (std::exception& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    throw;
  } catch (...) {
    std::cerr << argv[0] << " unknown caught an exception" << std::endl;
    HTTP::errorPage(std::cout, 404, "Exception", "Unknown Exception caught");
  }
  return 1;

}
