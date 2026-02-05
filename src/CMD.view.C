#include <CMD.H>
#include <CGI.H>
#include <HTML.H>
#include <HTTP.H>
#include <DataDB.H>
#include <cmath>

namespace {
  const double infinity(strtod("INFINITY", 0));

  class Datum {
  public:
    double flow;
    double gage;
    double temp;
    double inflow;
    double outflow;

    Datum(const double f, 
          const double g = infinity, 
          const double t = infinity,
	  const double i = infinity,
	  const double o = infinity) 
      : flow(f)
      , gage(g)
      , temp(t) 
      , inflow(i) 
      , outflow(o) 
    {}
  };

  void viewInput(HTML& html, const std::string& hash, const time_t start, const time_t stop) {
    html << "<form method=\"get\" action=\"" << Paths::DocumentRoot << "\">" << std::endl;
    html << "<div>" << std::endl;
    html << "<input type=\"hidden\" name=\"v\" value=\"" << hash << "\" />" << std::endl;
    html << "Start <input type=\"text\" maxlength=\"10\" size=\"10\" name=\"start\" value=\"" 
         <<  Convert::toString(start, "%Y%m%d") << "\" />" << std::endl;
    html << "Stop <input type=\"text\" maxlength=\"10\" size=\"10\" name=\"stop\" value=\"" 
         <<  Convert::toString(stop, "%Y%m%d") << "\" />" << std::endl;
    html << "<input type=\"submit\" name=\"update\" />" << std::endl;
    html << "</div>" << std::endl;
    html << "</form>" << std::endl;
  }
}

int
CMD::view(const CGI& cgi, 
          const std::string& hash) 
{
  std::string dbName, displayName;

  if (!loadMaster(hash, displayName, dbName))
    return 1;

  const time_t now(time(0));
  time_t start;
  time_t stop;

  if (cgi.isSet("start")) {
    const time_t stime(Convert::toTime(cgi("start")));
    if (stime != -1)
      start = stime;
  } else
    start = now - (30 * 24 * 60 * 60);

  if (cgi.isSet("stop")) {
    const time_t etime(Convert::toTime(cgi("stop")));
    if (etime != -1)
      stop = etime;
  } else
    stop = now;

  DataDB data;

  std::string criteria("unix_timestamp(time) >= " + Convert::toStr(start) + " and "
                       "unix_timestamp(time) <= " + Convert::toStr(stop));

  DataDB::tRecords inflow(data.tableExists(dbName, DataDB::INFLOW, false) ?
                          data.records(dbName, DataDB::INFLOW, criteria) : DataDB::tRecords());
  DataDB::tRecords outflow(data.tableExists(dbName, DataDB::OUTFLOW, false) ?
                           data.records(dbName, DataDB::OUTFLOW, criteria) : DataDB::tRecords());
  DataDB::tRecords flow(data.tableExists(dbName, DataDB::FLOW, false) ?
                        data.records(dbName, DataDB::FLOW, criteria) : DataDB::tRecords());
  DataDB::tRecords gage(data.tableExists(dbName, DataDB::GAGE, false) ?
                        data.records(dbName, DataDB::GAGE, criteria) : DataDB::tRecords());
  DataDB::tRecords temp(data.tableExists(dbName, DataDB::TEMPERATURE, false) ?
                        data.records(dbName, DataDB::TEMPERATURE, criteria) : DataDB::tRecords());

  if (inflow.empty() && outflow.empty() && flow.empty() && gage.empty() && temp.empty()) {
    HTTP::errorPage(std::cout, 404, "Database contains no data",
                    "Database(" + dbName + ") does not contain any data");
    return 1;
  }

  typedef std::map<time_t, Datum, std::greater<time_t> > tData;
  tData sortedData;

  for (DataDB::tRecords::const_iterator et(flow.end()), it(flow.begin()); it != et; ++it) 
    sortedData.insert(std::make_pair(it->when, Datum(Convert::strTo<double>(it->value))));

  for (DataDB::tRecords::const_iterator et(gage.end()), it(gage.begin()); it != et; ++it) {
    tData::iterator jt(sortedData.find(it->when));
    if (jt == sortedData.end()) 
      sortedData.insert(std::make_pair(it->when, Datum(infinity, Convert::strTo<double>(it->value))));
    else
      jt->second.gage = Convert::strTo<double>(it->value);
  }

  for (DataDB::tRecords::const_iterator et(temp.end()), it(temp.begin()); it != et; ++it) {
    tData::iterator jt(sortedData.find(it->when));
    if (jt == sortedData.end()) 
      sortedData.insert(std::make_pair(it->when, Datum(infinity, infinity, 
                                                       Convert::strTo<double>(it->value))));
    else
      jt->second.temp = Convert::strTo<double>(it->value);
  }

  for (DataDB::tRecords::const_iterator et(inflow.end()), it(inflow.begin()); it != et; ++it) {
    tData::iterator jt(sortedData.find(it->when));
    if (jt == sortedData.end()) 
      sortedData.insert(std::make_pair(it->when, Datum(infinity, infinity, infinity,
                                                       Convert::strTo<double>(it->value))));
    else
      jt->second.inflow = Convert::strTo<double>(it->value);
  }

  for (DataDB::tRecords::const_iterator et(outflow.end()), it(outflow.begin()); it != et; ++it) {
    tData::iterator jt(sortedData.find(it->when));
    if (jt == sortedData.end()) 
      sortedData.insert(std::make_pair(it->when, Datum(infinity, infinity, infinity, infinity,
                                                       Convert::strTo<double>(it->value))));
    else
      jt->second.outflow = Convert::strTo<double>(it->value);
  }

  HTML html(HTTP::compressable());

  html.startHead();

  html << "<link rel=\"shortcut icon\" href=\""
       << Paths::DocumentRoot << "zen_favicon.ico\" type=\"image/x-icon\" />"
       << std::endl;

  html.title("Database for " + displayName);
  html << "<script type=\"text/javascript\" src=\"" << Paths::JSRoot 
	 << "sort.js\"></script>" << std::endl;
  html.endHead();
  html.startBody();

  html << "<h1>" << displayName << "</h1>" << std::endl;

  viewInput(html, hash, start, stop);

  html << "<table border=\"1\" id=\"tbl\" class=\"sortable\">" << std::endl;
  html << "<thead>" << std::endl;

  const std::string dateLabel("Date");
  const std::string inflowLabel("<a href='#Units'>Inflow<br />CFS</a>");
  const std::string outflowLabel("<a href='#Units'>Outflow<br />CFS</a>");
  const std::string flowLabel("<a href='#Units'>Flow<br />CFS</a>");
  const std::string gageLabel("<a href='#Units'>Gauge<br />Feet</a>");
  const std::string tempLabel("<a href='#Units'>Temp<br />F</a>");

  html << "<tr><th>" << dateLabel << "</th>";
  if (!flow.empty()) 
    html << "<th>" << flowLabel << "</th>";
  if (!gage.empty())
    html << "<th>" << gageLabel << "</th>";
  if (!temp.empty())
    html << "<th>" << tempLabel << "</th>";
  if (!inflow.empty()) 
    html << "<th>" << inflowLabel << "</th>";
  if (!outflow.empty()) 
    html << "<th>" << outflowLabel << "</th>";
  html << "</tr></thead>" << std::endl;

  html << "<tfoot>" << std::endl;
  html << "<tr><th>" << dateLabel << "</th>";
  if (!flow.empty()) 
    html << "<th>" << flowLabel << "</th>";
  if (!gage.empty())
    html << "<th>" << gageLabel << "</th>";
  if (!temp.empty())
    html << "<th>" << tempLabel << "</th>";
  if (!inflow.empty()) 
    html << "<th>" << inflowLabel << "</th>";
  if (!outflow.empty()) 
    html << "<th>" << outflowLabel << "</th>";
  html << "</tr></tfoot>" << std::endl;

  html << "<tbody>" << std::endl;

  for (tData::const_iterator et(sortedData.end()), it(sortedData.begin()); it != et; ++it) {
    html << "<tr><td>" << Convert::toString(it->first, "%m/%d/%Y %H:%M") << "</td>";
    if (!flow.empty()) {
      html << "<td>";
      if (finite(it->second.flow)) 
        html << it->second.flow;
      else 
        html << "&nbsp;";
      html << "</td>";
    }
    if (!gage.empty()) {
      html << "<td>";
      if (finite(it->second.gage)) 
        html << it->second.gage;
      else 
        html << "&nbsp;";
      html << "</td>";
    }
    if (!temp.empty()) {
      html << "<td>";
      if (finite(it->second.temp)) 
        html << it->second.temp;
      else 
        html << "&nbsp;";
      html << "</td>";
    }
    if (!inflow.empty()) {
      html << "<td>";
      if (finite(it->second.inflow)) 
        html << it->second.inflow;
      else 
        html << "&nbsp;";
      html << "</td>";
    }
    if (!outflow.empty()) {
      html << "<td>";
      if (finite(it->second.outflow)) 
        html << it->second.outflow;
      else 
        html << "&nbsp;";
      html << "</td>";
    }
    html << "</tr>" << std::endl;
  }

  html << "</tbody>" << std::endl;

  html << "</table>" << std::endl;

  html << "<p />" << std::endl;

  viewInput(html, hash, start, stop);

  html.end();

  HTTP http(std::cout, &html);
  http.content();
  http.modified(now);
  http.expires(now + 3600);

  return 0;
}
