#include <genPlot.H>
#include <CGI.H>
#include <HTTP.H>
#include <HTML.H>
#include <Convert.H>
#include <InfoDB.H>
#include <PageDB.H>
#include <DataDB.H>
#include <CompressString.H>
#include <ReadFile.H>
#include <File.H>
#include <MakePlot.H>
#include <Points.H>
#include <Canvas.H>
#include <iostream>
#include <map>

/*
namespace {
  int genErrorPage(Canvas& canvas, const std::string txt) {
    const time_t now(time(0));
    const Point pt(0.5,0.5);
    canvas.text(txt, pt);
    const bool qCompress(HTTP::compressable());
    const std::string zstr(qCompress ? Compress::string(canvas.str()) : std::string());
    HTTP http(std::cout, 0);
    http.content(canvas.mimetype());
    http.modified(now);
    http.expires(now + 3600);
    if (qCompress) {
      http.encoding("gzip");
      http.length(zstr.size());
    } else {
      http.length(canvas.size());
    }
    std::cout << (qCompress ? zstr : canvas.str());

    return 1;
  }
} // Namespace anonymous
*/

int
genPlot(Canvas& canvas)
{
  const CGI cgi;

  const std::string& hash(cgi.get(cgi.isSet("h") ? "h" : "hash", std::string()));
  std::string type(cgi.get(cgi.isSet("t") ? "t" : "type", std::string()));
  std::string label(cgi.get("label", std::string()));
  const std::string sdate(cgi.get("sdate", std::string()));
  const std::string edate(cgi.get("edate", std::string()));

  size_t daysBack(cgi.isSet("daysback") ? Convert::strTo<size_t>(cgi.get("daysback")) : 10);

  if (!daysBack) daysBack = 10; 

  if (hash.empty()) {
    HTTP::errorPage(std::cout, 404, "Hash not set", "Hash not set");
    return 1;
  }
  
  if (type.empty()) {
    HTTP::errorPage(std::cout, 404, "Type not set", "Type not set");
    return 1;
  }

  if (type == "f") {
    type = "flow";
  } else if (type == "g") {
    type = "gage";
  } else if (type == "t") {
    type = "temperature";
  }

  if (label.empty()) {
    if (type == "flow") {
      label = "Flow (CFS)";
    } else if (type == "gage") {
      label = "Gauge (Ft)";
    } else if (type == "temperature") {
      label = "Temperature (F)";
    }
  }

  InfoDB info;
  const InfoDB::tRecords& dbNames(info.master("db_name,display_name,gauge_location", 
                                              "HashValue='" + hash + "'"));

  if (dbNames.size() != 1) {
    if (dbNames.empty()) 
      HTTP::errorPage(std::cout, 404, "No database found", "No database found for (" + hash + ")");
    else
      HTTP::errorPage(std::cout, 404, "Too many databases found", 
                      "Too many databases found for (" + hash + ")");
    return 1;
  }
 
  const std::string& dbName(dbNames[0][0]);
  const std::string& riverName(dbNames[0][1]);
  const std::string& gageLocation(dbNames[0][2]);
  const std::string displayName(riverName + (gageLocation.empty() ? "" : ("@" + gageLocation)));

  DataDB data;
  const DataDB::TYPE dataType(data.type(type));

  if (dataType == DataDB::UNKNOWN) {
    HTTP::errorPage(std::cout, 404, "Unrecognized data type",
                    "Unrecognized data type(" + type + ")");
    return 1;
  }

  std::string criteria("time > now() - interval " + Convert::toStr(daysBack) + 
                       " day and time < now()");

  if (!sdate.empty() && !edate.empty()) {
    time_t stime(Convert::toTime(sdate));
    time_t etime(Convert::toTime(edate));
    if (stime > etime) {
      const time_t ttime(stime);
      stime = etime;
      etime = ttime;
    }
    criteria = "time < FROM_UNIXTIME(" +
               Convert::toStr(etime + 86400) + 
               ") and time >= FROM_UNIXTIME(" + 
               Convert::toStr(stime) + ")";
  }

  const DataDB::tRecords values(data.tableExists(data.tableName(dbName, dataType)) ?
                                data.records(dbName, dataType, criteria) : DataDB::tRecords());

  if (values.empty()) {
    // std::ostringstream oss;
    // oss << "No records found for " << dbName << " of type " << type 
        // << " for hash " << hash; 
    // return genErrorPage(canvas, oss.str());
    HTTP::errorPage(std::cout, 404, "No records found for " + dbName,
                    "No records found for (" + dbName + ") of type (" + type + 
                    ") for hash (" + hash + ")");
    return 1;
  }

  // { // TPW
    // std::ostringstream oss;
    // oss << "n points=" << values.size() << std::endl;
    // for (DataDB::tRecords::size_type i = 0; i < values.size(); ++i) {
      // oss << "<Br>i=" << i << " time=" << values[i].when 
          // << " value=" << values[i].value
          // << std::endl;
    // }
    // HTTP::errorPage(std::cout, 404, "Diagnostic", oss.str());
    // return 1;
  // }

  Points points(values.size());

  for (DataDB::tRecords::size_type i = 0; i < values.size(); ++i) {
    points[i].x(values[i].when);
    points[i].y(Convert::strTo<double>(values[i].value));
  }

  const time_t now(time(0));

  if (MakePlot(canvas, points, displayName, label)) {
    const bool qCompress(HTTP::compressable());
    const std::string zstr(qCompress ? Compress::string(canvas.str()) : std::string());
    {
      HTTP http(std::cout, 0);
      http.content(canvas.mimetype());
      http.modified(now);
      http.expires(now + 3600);
      if (qCompress) {
        http.encoding("gzip");
        http.length(zstr.size());
      } else
        http.length(canvas.size());
    }
    std::cout << (qCompress ? zstr : canvas.str());

    return 0;
  }

  HTTP::errorPage(std::cout, 404, "Generation of plot failed",
                  "Plot generation failed for (" + dbName + ") of type (" + type + ") hash (" +
                  hash + ")");
  return 1;
}
