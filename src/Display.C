#include <Paths.H>
#include <Display.H>
#include <ReadFile.H>
#include <Tokenize.H>
#include <File.H>
#include <PageArchive.H>
#include <HTML.H>
#include <String.H>
#include <iostream>
#include <sstream>
#include <cwchar>

namespace {
  std::string displayField(const std::string& f) {
    return ("<th>" + 
            (f.empty() ? std::string("&nbsp;") : 
                         String::replace(f, "&", "&amp;")) + 
            "</th>");
  }
}

// Construct actual web pages

Display::Record::Record(const Builder& builder, const InfoDB::tRecord& record) 
  : mTime(0) 
{
  InfoDB::tRecord::size_type i(0);

  for (Builder::const_iterator et(builder.end()), it(builder.begin()); it != et; ++it)
    if (it->use('f') && !it->field().empty())
      mMap.insert(std::make_pair(it->field(), record[i++]));
}

const std::string& 
Display::Record::operator () (const std::string& key) const 
{
  tMap::const_iterator it(mMap.find(key));
  return it == mMap.end() ? mBlank : it->second;
}

void 
Display::Record::operator () (const std::string& key, 
                              const std::string& value) 
{
  tMap::iterator it(mMap.find(key));
  if (it == mMap.end())
    mMap.insert(std::make_pair(key, value));
  else
    it->second = value;
}

void 
Display::Record::operator () (const std::string& key, 
                              const time_t ts) 
{
  mTime = ts;
  char buffer[128];
  struct tm *tm(localtime(&ts));
  strftime(buffer, sizeof(buffer), "%m/%d %H:%M", tm);
  (*this)(key, buffer);
}

std::ostream& 
operator << (std::ostream& os, 
             const Display::Record& r)
{
  for (Display::Record::tMap::const_iterator et(r.mMap.end()), it(r.mMap.begin()); it != et; ++it) 
    os << it->first << ' ' << it->second << std::endl;

  return os;
}

void
Display::addStates(const Record& record)
{
  const Tokenize states(record("state"));

  for (Tokenize::const_iterator et(states.end()), it(states.begin()); it != et; ++it)
    mStates.insert(*it);
}

void
Display::getLatest(Record& record,
                   time_t& ts,
                   const DataDB::TYPE type,
                   const std::string& id)
{
  time_t when;
  double value;
  bool changeFlag;
  double change;
  const std::string maxBack("time > now() - interval 60 day");
  const std::string& dbName(record("db_name"));

  if (!dbName.empty() && mData.latest(dbName, type, maxBack, when, value, changeFlag, change)) {
    if (when) {
      ts = (ts && ts > when) ? ts : when;
      record(id, Convert::toStr(value));
    }
    if (changeFlag)
      record(id + ".change", Convert::toStr(change));
  }
}

bool
Display::getLatest(Record& record)
{
  time_t ts(0);
  getLatest(record, ts, DataDB::FLOW, "flow");
  getLatest(record, ts, DataDB::GAGE, "gage");
  getLatest(record, ts, DataDB::TEMPERATURE, "temperature");

  if (ts) 
    record("time", ts);

  return ts;
}

std::string 
Display::makeTableName(const std::string& state,
                       const std::string& key)
{
  const std::string id(state.empty() ? "All" : state);
  const std::string suffix(mParams("files." + key + ".suffix"));
  return id + suffix;
}

static void
updateClass(Display::Record& record)
{
  const std::string& flow(record("class_flow"));

  if (!flow.empty()) {
    const Tokenize f(flow, ",");
    for (Tokenize::const_iterator et(f.end()), it(f.begin()); it != et; ++it) {
      const Tokenize a(*it);
      if (!a.empty()) {
        const std::string& low(a.size() < 2 || a[1].empty() ? "-1e30" : a[1]);
        const std::string& high(a.size() < 3 || a[2].empty() ? "-1e30" : a[2]);
        const bool qFlow(low.find("ft") == low.npos && high.find("ft") == high.npos);
        const std::string data(qFlow ? record("flow") : record("gage"));
        if (!data.empty()) {
          const double l(atof(low.c_str()));
          const double h(atof(high.c_str()));
          const double g(atof(data.c_str()));
          if ((l <= g) && (g <= h)) {
            record("class", a[0]);
            return;
          }
        }
      }
    }
  }
}

static void
updateStatus(Display::Record& record)
{
  record("status.colored", "<th>&nbsp;</th>");

  const std::string& low(record("low_flow"));
  if (low.empty())
    return;

  const std::string& high(record("high_flow"));
  if (high.empty())
    return;

  const bool qFlow(low.find("ft") == low.npos && high.find("ft") == high.npos);

  const std::string& data(qFlow ? record("flow") : record("gage"));
  if (data.empty()) 
    return;

  const double g(atof(data.c_str()));
  const double l(low.empty() ? -1e30 : atof(low.c_str()));
  const double h(high.empty() ? 1e30 : atof(high.c_str()));

  if (g < l) { // Low
    record("status", "Low");
    record("status.colored", "<th style=\"color: rgb(0,0,128)\">Low</th>");
  } else if (g > h) { // High
    record("status", "High");
    record("status.colored", "<th style=\"color: Red\">High</th>");
  } else { // Okay
    record("status", "Okay");
    record("status.colored", "<th style=\"color: Green\">Okay</th>");
  }
}

static std::string
currentTime()
{
  const time_t now(time(0));
  const tm *tm(localtime(&now));
  char buffer[128];
  strftime(buffer, sizeof(buffer), "%A, %B, %d %Y at %H:%m", tm);
  return buffer;
}

static std::string
displayPlot(const Display::Record& record,
            const std::string& key,
            const std::string& hash,
            const std::string& type)
{
  const std::string& value(record(key));

  if (value.empty())
    return std::string("&nbsp;");

  std::string str("<a href=\"" + Paths::DocumentRoot + "?" + type + "=" + hash + "\">" + value + "</a>");

  const std::string& change(record(key + ".change"));

  if (change.empty())
    return str;

  const double delta(Convert::strTo<double>(change) * 100);
  const int rounded((int) delta);

  if (rounded) { // Moving about
    static const char *colorMap[] = {"silver", "lime", "green", "teal", 
                                     "navy", "blue", "aqua", 
	                             "purple", "maroon", "red", "fuchsia"};
    static size_t nEntries(sizeof(colorMap) / sizeof(char *));
    const std::string direction(rounded > 0 ? "&uarr;" : "&darr;");
    const size_t index(abs(rounded) - 1);
    const std::string color(index >= nEntries ? colorMap[nEntries - 1] : colorMap[index]);
    str += "<span style=\"color:" + color + "\">" + direction + "</span>";
  }
  return str; 
}

static std::string
displayName(const Display::Record& record)
{
  const std::string& hash(record("hashValue"));
  const std::string& name(record("display_name"));
  const std::string& calc(record("calc_expr"));

  return "<th><a href=\"" + Paths::DocumentRoot + "?D=" + hash + "#A" + hash + "\">" + name + 
         (calc.empty() ? "" : "<span style=\"color:red\">(est)</span>") +
         "</a></th>";
}

static std::string
displayDate(const Display::Record& record)
{
  static const time_t ref(time(0) - (4 * 24 * 60 * 60)); // 4 days ago
  std::string str("<th");

  if (record.time() && (record.time() < ref))
    str += " style=\"color: Red\"";

  return str + ">" + record("time") + "</th>";
}

static std::string
makeTitle(const std::string& state)
{
  return state.empty() ? "All" : state;
}

static bool
isState(const std::string& state, 
        const Display::Record& record)
{
  return state.empty() || (record("state").find(state) != std::string::npos);
}

void 
Display::html(const std::string& state, 
              std::ostream& os,
              const bool fullPage,
              const bool tFoot)
{
  const char useKey('h');
  bool qRecords(false);

  for (Builder::iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
    if (!jt->use(useKey))
      continue;
    jt->colUsed(false);
    for (tRecords::const_iterator et(mRecords.end()), it(mRecords.begin()); it != et; ++it) {
      const Record& record(*it);
      if (isState(state, record) && !record(jt->field()).empty()) {
        jt->colUsed(true);
        qRecords |= true;
        break;
      }
    }
  }

  if (fullPage) {
    os << HTML::XHTML11(); // Get proper document type
    const std::string title(makeTitle(state));
    os << "<head>" << std::endl;
    os << "<link rel=\"shortcut icon\" href=\""
       << Paths::DocumentRoot << "zen_favicon.ico\" type=\"image/x-icon\" />" 
       << std::endl;
    os << "<title>" << title << " levels</title>" << std::endl;
    os << "<script type=\"text/javascript\" src=\"" << Paths::JSRoot << "sort.js\"></script>" << std::endl;
    os << "</head>" << std::endl;
    os << "<body>" << std::endl;
    os << "<div>" << std::endl;
    os << "<h1>" << title << "</h1>" << std::endl;
    os << "<h3>Please contact " << std::endl;
    os << Paths::Maintainer << " with any comments about this page.</h3>" << std::endl;
    {
      const std::string headName(mParams.fileName("templateDir", "gage.html.pre.filename"));
      if (!headName.empty()) 
        os << ReadFile(headName, false);
    }

    const std::string dir(mParams.dirName("webPageDir"));
    const std::string mapName(dir + "/" + title + ".png");
    if (File::exists(mapName)) 
      os << "<h2><a href=\"" << Paths::DocumentRoot << File::tail(mapName) << "\">" << title 
         << " map</a></h2>" << std::endl;
  }

  if (qRecords) {
    os << "<table border=\"1\">" << std::endl;
    os << "<thead><tr>";

    for (Builder::const_iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
      if (jt->colUsed() && jt->use(useKey)) 
        os << "<th>" << jt->html() << "</th>";
    }
  
    os << "</tr>" << std::endl;
    os << "</thead>" << std::endl;
 
    if (tFoot) {
      os << "<tfoot><tr>";
      for (Builder::const_iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt)
        if (jt->colUsed() && jt->use(useKey)) 
          os << "<th>" << jt->html() << "</th>";
  
      os << "</tr></tfoot>" << std::endl;
    }

    os << "<tbody>" << std::endl;
 
    for (tRecords::const_iterator et(mRecords.end()), it(mRecords.begin()); it != et; ++it) {
      const Record& record(*it);
      if (isState(state, record)) {
        const std::string hash(record("hashValue"));
        os << "<tr>";
        for (Builder::const_iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
          if (jt->colUsed() && jt->use(useKey)) {
            if (jt->type("status")) os << record("status.colored");
            else if (jt->type("name")) os << displayName(record);
            else if (jt->type("date")) os << displayDate(record);
            else if (jt->type("text")) os << displayField(record(jt->field()));
            else if (jt->type("flow"))
              os << "<th>" << displayPlot(record, jt->field(), hash, "f") << "</th>";
            else if (jt->type("gage"))
              os << "<th>" << displayPlot(record, jt->field(), hash, "g") << "</th>";
            else if (jt->type("temp"))
              os << "<th>" << displayPlot(record, jt->field(), hash, "t") << "</th>";
            else os << displayField(record(jt->field()));
          }
        }
        os << "</tr>" << std::endl;
      }
    }

    os << "</tbody>" << std::endl;

    os << "</table>" << std::endl;
  } // qRecords

  if (fullPage) {
    os << "<h3>Generated " << currentTime() << "</h3>" << std::endl;
    {
      const std::string filename(mParams.fileName("templateDir", "gage.html.post.filename"));
      if (!filename.empty()) 
        os << ReadFile(filename, false);
    }
    os << "</div>" << std::endl;
    os << "</body>" << std::endl;
    os << "</html>" << std::endl;
  }
}

void 
Display::html(const std::string& state)
{
  std::ostringstream os;
  html(state, os, true, true);

  PageArchive::dumpPage(makeTableName(state, "html"), "text/html", 60 * 60, os.str());
}

static std::string
textField(const std::string& str,
          const std::string::size_type length)
{
  if (str.size() >= length) 
    return str.substr(0, length);

  std::string a(length, ' ');
  a.replace((length - str.size()) / 2, str.size(), str);
  return a;
}

void 
Display::text(const std::string& state)
{
  std::ostringstream os;
  const std::string delim("|");
  const char useKey('t');

  for (Builder::iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
    if (!jt->use(useKey))
      continue;
    jt->colUsed(false);
    for (tRecords::const_iterator et(mRecords.end()), it(mRecords.begin()); it != et; ++it) {
      const Record& record(*it);
      if (isState(state, record) && !record(jt->field()).empty()) {
        jt->colUsed(true);
        break;
      }
    }
  }

  {
    std::string space;

    for (Builder::const_iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
      if (jt->colUsed() && jt->use(useKey)) {
        os << space << textField(jt->text(), jt->length());
        space = delim;
      }
    }
    os << std::endl;
  }

  for (tRecords::const_iterator et(mRecords.end()), it(mRecords.begin()); it != et; ++it) {
    const Record& record(*it);
    if (isState(state, record)) {
      std::string space;
      for (Builder::const_iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
        if (jt->colUsed() && jt->use(useKey)) {
          os << space << textField(record(jt->field()) + 
                                   (jt->field() == "display_name" && 
                                    !record("calc_expr").empty() ? "(est)" : ""), 
                                   jt->length());
          space = delim;
        }
      }
      os << std::endl;
    }
  }

  PageArchive::dumpPage(makeTableName(state, "text"), "text/plain", 60 * 60, os.str());
}

void 
Display::csv(const std::string& state)
{
  const std::string delim(",");
  std::ostringstream os;
  const char useKey('c');

  for (Builder::iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
    if (!jt->use(useKey))
      continue;
    jt->colUsed(false);
    for (tRecords::const_iterator et(mRecords.end()), it(mRecords.begin()); it != et; ++it) {
      const Record& record(*it);
      if (isState(state, record) && !record(jt->field()).empty()) {
        jt->colUsed(true);
        break;
      }
    }
  }

  {
    std::string space;

    for (Builder::const_iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
      if (jt->colUsed() && jt->use(useKey)) {
        os << space << jt->text();
        space = delim;
      }
    }
    os << std::endl;
  }

  for (tRecords::const_iterator et(mRecords.end()), it(mRecords.begin()); it != et; ++it) {
    const Record& record(*it);
    if (isState(state, record)) {
      std::string space;
      for (Builder::const_iterator jte(mBuilder.end()), jt(mBuilder.begin()); jt != jte; ++jt) {
        if (jt->colUsed() && jt->use(useKey)) {
          os << space << record(jt->field()) + 
                         (jt->field() == "display_name" && 
                          !record("calc_expr").empty() ? "(est)" : ""), 
          space = delim;
        }
      }
      os << std::endl;
    }
  }

  PageArchive::dumpPage(makeTableName(state, "csv"), "text/plain", 60 * 60, os.str());
}

Display::Display(const std::string& builderDB,
                 const std::string& criteria)
  : mBuilder(mInfo, builderDB)
{
  std::string fields;

  for (Builder::const_iterator et(mBuilder.end()), it(mBuilder.begin()); it != et; ++it) 
    if (it->use('f') && !it->field().empty())
      fields += (fields.empty() ? "" : ",") + it->field();
 
  const InfoDB::tRecords& records(mInfo.master(fields, criteria));

  for (InfoDB::tRecords::const_iterator et(records.end()), it(records.begin()); it != et; ++it) {
    Record record(mBuilder, *it);
    if (getLatest(record))  {
      updateClass(record);
      updateStatus(record);
      addStates(record);
      mRecords.push_back(record);
    }
  }
}

Display::Display(const std::string& builderDB,
                 const std::string& criteria,
                 const std::string& hashes)
  : mBuilder(mInfo, builderDB)
{
  std::string fields;

  for (Builder::const_iterator et(mBuilder.end()), it(mBuilder.begin()); it != et; ++it) 
    if (it->use('f') && !it->field().empty())
      fields += (fields.empty() ? "" : ",") + it->field();
 
  const InfoDB::tRecords& records(mInfo.master(fields, 
                                               criteria + 
                                               (hashes.empty() ? "" : 
                                                (" and find_in_set(hashValue,'" + hashes + "')"))));

  typedef std::map<std::string, int> tMap;
  tMap m;

  for (InfoDB::tRecords::size_type size(records.size()), i(0); i < size; ++i) {
    Record record(mBuilder, records[i]);
    m.insert(std::make_pair(record("hashValue"), i));
  }
 
  const Tokenize tokens(hashes, " ,\t\n");

  for (Tokenize::const_iterator et(tokens.end()), it(tokens.begin()); it != et; ++it) {
    tMap::const_iterator mt(m.find(*it));
    if (mt != m.end()) {
      Record record(mBuilder, records[mt->second]);
      if (getLatest(record))  {
        updateClass(record);
        updateStatus(record);
        addStates(record);
        mRecords.push_back(record);
      }
    }
  }
}
