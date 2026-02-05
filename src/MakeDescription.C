#include <MakeDescription.H>
#include <InfoDB.H>
#include <DataDB.H>
#include <ParameterDB.H>
#include <Description.H>
#include <Display.H>
#include <Tokenize.H>
#include <String.H>
#include <ReadFile.H>
#include <iostream>

namespace {
  std::string encodeURL(const std::string& url) {
    std::string a(String::replace(url, "&", "&amp;"));
    String::replaceInPlace(a, "<", "&lt;");
    String::replaceInPlace(a, ">", "&gt;");
    return a;
  }

  std::string handleDB(const std::string& hash,
                       const std::string& dbName, 
                       const std::string& mergedDBs,
                       DataDB& data)
  {
    if (dbName.empty())
      return std::string();

    std::string msg("<a href=\"?v=" + hash + "\">Database</a>\n");

    DataDB::tURLs urls(data.url(dbName));

    typedef std::set<std::string> tSet;
    tSet uniq;

    for (DataDB::tURLs::const_iterator et(urls.end()), it(urls.begin()); it != et; ++it)
      if (it->substr(0,5) != "file:")
        uniq.insert(*it); // Get rid of duplicates and order

    Tokenize names(mergedDBs);

    for (Tokenize::const_iterator et(names.end()), it(names.begin()); it != et; ++it) {
      DataDB::tURLs a(data.url(*it));
      for (DataDB::tURLs::const_iterator jte(urls.end()), jt(urls.begin()); jt != jte; ++jt)
        if (jt->substr(0,5) != "file:")
          uniq.insert(*jt); // Get rid of duplicates and order
    }

    if (!uniq.empty()) {
      msg += "<br /><b>Data Sources:</b>";

      std::string comma;
      for (tSet::const_iterator et(uniq.end()), it(uniq.begin()); it != et; ++it) {
        const std::string url(encodeURL(*it));
        msg += comma + "<a href=\"" + url + "\">" + url + "</a>\n";
        comma = ", ";
      }
    }

    return msg;
  }

  std::string handleCalc(InfoDB& info,
                         const std::string& expr,
                         const std::string& time)
  {
    if (expr.empty() || time.empty())
      return "";

    const Tokenize names(time);
    std::string msg(expr);

    for (Tokenize::const_iterator et(names.end()), it(names.begin()); it != et; ++it) {
      const std::string& name(*it);
      const std::string hash(name.substr(0, name.find("::")));
      const std::string dname(info.displayName(hash));
      const std::string uname(dname.empty() ? 
                              name.substr(name.find("::") + 2) : 
                              (dname + name.substr(name.rfind("::"))));
 
      for (std::string::size_type i, pos = 0; (i = msg.find(name, pos)) != msg.npos;) {
        const std::string str("<a href=\"?D=" + hash + "\">" + uname + "</a>");
        msg.replace(i, name.size(), str);
        pos += str.size();
      }
    }

    return msg;
  }

  std::string handleURL (const std::string& urls)
  {
    Tokenize tokens(urls);
    std::string msg;

    for (Tokenize::const_iterator et(tokens.end()), it(tokens.begin()); it != et; ++it) {
      const std::string url(encodeURL(*it));
      msg += "<a href=\"" + url + "\">" + url + "</a>\n";
    }

    return msg;
  }
}

MakeDescription::MakeDescription(std::ostream& os,
                                 const bool fullPage,
                                 const bool editPage,
                                 const bool pointToFullPage)
  : mInfo(),
    mDescription(mInfo),
    mOS(os),
    mEdit(editPage),
    mFullPage(pointToFullPage)
{
  if (mDescription.empty())
    throw "No description fields found";

  if (fullPage) {
    ParameterDB params;
    const std::string head(params.fileName("templateDir", "guide.pre.filename"));
    mTail = params.fileName("templateDir", "guide.post.filename");
    if (!head.empty())
      os << ReadFile(head, false);
  }

  for (Description::size_type size(mDescription.size()), i(0); i < size; ++i) 
    mFields += ((i == 0) ? "" : ",") + mDescription[i].name();
}

bool
MakeDescription::close(const std::string& hash)
{
  if (mFullPage) { 
    mOS << "<hr /><h1><a href=\"?d";
    if (!hash.empty())
      mOS << "#A" << hash;
    mOS << "\">Full database of river descriptions</a></h1>" << std::endl;
  }

  if (!mTail.empty()) {
    mOS << ReadFile(mTail, false);
    mTail.clear();
  }
  return true;
}

bool
MakeDescription::master(const std::string& hash, const std::string& criteria)
{
  std::string aCriteria(criteria);

  if (!hash.empty()) {
    const InfoDB::tRecords& records(mInfo.master("river_name", 
                                                 "hashValue='" + hash + "'"));
    if (records.empty()) {
      aCriteria = (aCriteria.empty() ? std::string() : std::string(" and ")) +
                  "hashValue='" + hash + "'";
    } else {
      const std::string& riverName(records[0][0]);
      if (riverName == "") 
        aCriteria = (aCriteria.empty() ? std::string() : std::string(" and ")) +
                    "hashValue='" + hash + "'";
      else
        aCriteria = (aCriteria.empty() ? std::string() : std::string(" and ")) +
                    "river_name='" + riverName + "'";
    }
  } 

  const InfoDB::tRecords& records(mInfo.master(mFields, aCriteria));

  if (records.empty()) // Failed
    throw "Failed to get any entries with criteria(" + criteria + ")";

  return spew(records);
}

bool
MakeDescription::spew(const InfoDB::tRecords& records)
{
  for (InfoDB::tRecords::size_type size(records.size()), i(0); i < size; ++i) 
    if (!spew(records[i]))
      return false;

  return true;
}

bool
MakeDescription::spew(const InfoDB::tRecord& row)
{
  const std::string& hash(row[0]);
  const std::string& displayName(row[1]);
  
  mOS << std::endl;
  mOS << "<hr /><h1><a id=\"A" << hash << "\">" 
      << encodeURL(displayName) << "</a></h1>" << std::endl;

  if (mFullPage) { // If a subset, then generate flow lines
    Display d("Builder", "no_show is null and db_name is not null", hash);
    d.html(std::string(), mOS, false, false);
  }
  
  for (InfoDB::tRecord::size_type size(row.size()), j(2); j < size; ++j) { 
    const std::string& name(mDescription[j].name());
    const std::string& type(mDescription[j].type());
    const std::string& prefix(mDescription[j].prefix());
    const std::string& suffix(mDescription[j].suffix());

    if (type == "noop" || row[j].empty())
      continue; // Nothing to do
  
    mOS << "<b>" << (!prefix.empty() ? prefix : name) << ":</b>";
 
    if (type == "text") 
      mOS << encodeURL(row[j]);
    else if (type == "ptxt") 
      mOS << "<pre>" << std::endl << encodeURL(row[j]) << std::endl << "</pre>";
    else if (type == "URL") 
      mOS << handleURL(row[j]);
    else if (type == "DB")
      mOS << handleDB(hash, row[j], row[j-1], mData);
    else if (type == "calc")
      mOS << handleCalc(mInfo, row[j], row[j-1]);
    else 
      throw "Unrecognized type(" + type + ") in description record";

    mOS << suffix << std::endl;
  }

  if (mEdit)
    mOS << "<h2><a href=\"?e=" << hash << "\">Edit entry</a></h2>" << std::endl;

  return true;
}
