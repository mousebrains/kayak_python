#include <HTML.H>
#include <HTTP.H>
#include <CGI.H>
#include <Convert.H>
#include <InfoDB.H>
#include <DataDB.H>
#include <iostream>

int 
main (int argc,
      char **argv)
{
  const time_t now(time(0));
  const CGI cgi;
  const std::string hash(cgi.get("h", std::string()));
  const std::string type(cgi.get("t", std::string()));
  std::string typeName;
  std::string units;

  if (hash.empty()) { // No hash
    HTML html(HTTP::compressable(), true);
    html << "procData({error: 'Hash not set'});";
    HTTP http(std::cout, &html);
    http.content("text/javascript");
    http.modified(now);
    http.expires(now);
    return 1;
  }

  if (type.empty()) { // No hash
    HTML html(HTTP::compressable(), true);
    html << "procData({error: 'Type not set for hash " << hash << "'});";
    HTTP http(std::cout, &html);
    http.content("text/javascript");
    http.modified(now);
    http.expires(now);
    return 1;
  }

  if (type == "f") { // Flow
    typeName = "Flow";
    units = "CFS"; 
  } else if (type == "g") { // gauge
    typeName = "Gauge";
    units = "Feet"; 
  } else if (type == "t") { // temperature
    typeName = "Temperature";
    units = "C"; 
  } else {
    HTML html(HTTP::compressable(), true);
    html << "procData({error: 'Type(" << type 
         << ") unknown for hash " << hash << "'});";
    HTTP http(std::cout, &html);
    http.content("text/javascript");
    http.modified(now);
    http.expires(now);
    return 1;
  }

  time_t edate(cgi.isSet("edate") ? 
               Convert::toTime(cgi.get("edate")) : now);
  time_t sdate(cgi.isSet("sdate") ? 
               Convert::toTime(cgi.get("sdate")) : 
               edate - (14 * 86400));
  if (edate < sdate) {
    const time_t t(edate);
    edate = sdate;
    sdate = t;
  }

  InfoDB info;
  const InfoDB::tRecords& dbNames(info.master("db_name,display_name,gauge_location", 
                                              "HashValue='" + hash + "'"));

  if (dbNames.size() != 1) {
    HTML html(HTTP::compressable(), true);
    html << "procData({error: '";
    if (dbNames.empty()) {
      html << "No database found for (" << hash << ")";
    } else {
      html << "Too many databases found for (" << hash << "), " 
           << dbNames.size();
    }
    html << "'});";
    HTTP http(std::cout, &html);
    http.content("text/javascript");
    http.modified(now);
    http.expires(now);
    return 1;
  }
 
  const std::string& dbName(dbNames[0][0]);
  const std::string& riverName(dbNames[0][1]);
  const std::string& gageLocation(dbNames[0][2]);
  const std::string displayName(riverName + (gageLocation.empty() ? "" : ("@" + gageLocation)));

  DataDB data;
  const DataDB::TYPE dataType(data.type(typeName));

  if (dataType == DataDB::UNKNOWN) {
    HTML html(HTTP::compressable(), true);
    html << "procData({error: 'Unrecognized data type(" 
         << typeName
         << ")'});";
    HTTP http(std::cout, &html);
    http.content("text/javascript");
    http.modified(now);
    http.expires(now);
    return 1;
  }

  const std::string criteria("time < FROM_UNIXTIME(" +
                             Convert::toStr(edate + 86400) + 
                             ") and time >= FROM_UNIXTIME(" + 
                             Convert::toStr(sdate) + ")");

  const DataDB::tRecords values(data.tableExists(data.tableName(dbName, dataType)) ?
                                data.records(dbName, dataType, criteria) : DataDB::tRecords());

  if (values.empty()) {
    HTML html(HTTP::compressable(), true);
    html << "procData({error: 'No data records found for "
         << dbName << " of type(" << typeName << ") for hash (" << hash 
         << ") ["
         << Convert::toString(sdate) 
         << " to "
         << Convert::toString(edate) 
         << "]'});";
    HTTP http(std::cout, &html);
    http.content("text/javascript");
    http.modified(now);
    http.expires(now);
    return 1;
  }

  const time_t t0(values[values.size()-1].when);
  HTML html(HTTP::compressable(), true);

  html << "procData({"
       << "title:'" << riverName << (gageLocation.empty() ? "" : ("@" + gageLocation))  << "'"
       << ",ylabel:'" << typeName << "'"
       << ",units:'" << units << "'"
       << ",t0:" << t0
       << ",t:[";

  for (DataDB::tRecords::size_type i=0, e = values.size(); i < e; ++i) {
    html << ((i == 0) ? "" : ",") 
         << (values[i].when - t0);
  }
 
  html << "],y:[";

  for (DataDB::tRecords::size_type i=0, e = values.size(); i < e; ++i) {
    html << ((i == 0) ? "" : ",") 
         << Convert::strTo<double>(values[i].value);
  }

  html << "]});";

  HTTP http(std::cout, &html);
  http.content("text/javascript");

  http.modified(now);
  http.expires(now + 3600);

  return 0;
}
