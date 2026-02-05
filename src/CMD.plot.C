#include <CMD.H>
#include <DataDB.H>
#include <CGI.H>
#include <HTTP.H>
#include <HTML.H>
#include <Paths.H>

int 
CMD::plot(const CGI& cgi,
          const std::string& id,
          const std::string& hash, 
          const std::string& type, 
          const std::string& label) 
{
  std::string dbName, displayName;

  if (!loadMaster(hash, displayName, dbName))
    return 1;

  DataDB data;

  if (!data.tableExists(data.tableName(dbName, type))) {
    HTTP::errorPage(std::cout, 404, "No database found for " + dbName,
                    "No database found for (" + dbName + ") of type (" + type + 
                    ") for hash (" + hash + ")");
    return 1;
  }

  const time_t now(time(0));
  size_t daysback(cgi.isSet("daysback") ? Convert::strTo<size_t>(cgi.get("daysback")) : 10);
  if (!daysback) daysback = 10;
  const std::string daysBackStr(Convert::toStr(daysback));

  HTML html(HTTP::compressable());

  const std::string suffix("?hash=" + hash + "&amp;type=" + type + "&amp;label=" + label +
                           "&amp;daysback=" + daysBackStr);
  // const int width(800), height(500);

  html.head(displayName + " " + label);
  html << "<h1>" << displayName << " " << type << "</h1>" << std::endl;
  html << "<div id=\"plot\">" << std::endl;
  html << "<img src=\"" << Paths::CGIRoot << "png" << suffix 
	 << "\" alt=\"Your browser does not support PNG\" />" << std::endl;
  html << "</div>" << std::endl;
  html << "<form method=\"get\" action=\"" << Paths::DocumentRoot << "\">" << std::endl;
  html << "<p>" << std::endl;
  html << "<input type=\"hidden\" name=\"" << id << "\" value=\"" << hash 
       << "\" />" << std::endl;
  html << "<em>Days back</em><input type=\"text\" name=\"daysback\" value=\"" 
       << daysBackStr << "\" />" << std::endl;
  html << "</p>" << std::endl;
  html << "</form>" << std::endl;
 
  HTTP http(std::cout, &html);

  http.content();
  http.modified(now);
  http.expires(now + 24 * 3600);

  return 0;
}
