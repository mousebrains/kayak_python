#include <CMD.H>
#include <InfoDB.H>
#include <HTML.H>
#include <HTTP.H>
#include <URL.H>

int 
CMD::edit(const std::string& hash) 
{
  if (badHash(hash, " to edit")) 
    return 1;

  InfoDB info;
  const InfoDB::tRecords editFields(info.edit("type,field,width,height,description,footnote"));
  std::string fields;

  for (InfoDB::tRecords::size_type size(editFields.size()), i(0); i < size; ++i) {
    const std::string field(editFields[i][1].empty() ? "sort_key" : editFields[i][1]);
    fields += (fields.empty() ? "" : ",") + field;
  }

  const InfoDB::tRecords& records(info.master(fields, "HashValue='" + hash + "'"));

  if (records.size() != 1) {
    if (records.empty()) 
      HTTP::errorPage(std::cout, 404, "No database found", 
                      "No database found for (" + hash + ")");
    else
      HTTP::errorPage(std::cout, 404, "Too many databases found", 
                      "Too many databases found for (" + hash + ")");
    return false;
  }

  const InfoDB::tRecord& rec(records[0]);

  std::string displayName;
  for (InfoDB::tRecords::size_type size(editFields.size()), i(0); i < size; ++i) {
    if (editFields[i][1] == "display_name") 
      displayName = rec[i];
    else if (editFields[i][1] == "gauge_location") {
      if (!rec[i].empty())
        displayName += "@" + rec[i];
      break;
    }
  }

  if (displayName.empty()) {
    HTTP::errorPage(std::cout, 404, "Empty display name", "Empty display name");
    return 1;
  }

  HTML html(HTTP::compressable());

  html.startHead();
  html.title(displayName);
  html.endHead();
  html.startBody();
  html << "<div>" << std::endl;
  html << "<h1>" << displayName << "</h1>" << std::endl;

  html << "<form action=\"" << Paths::CGIRoot << "submit\" method=\"post\">" << std::endl;
  html << "<div>" << std::endl;
  html << "<input type=\"hidden\" value=\"" << hash << "\" name=\"hash\" />" << std::endl;

  html << "<table>" << std::endl;

  for (InfoDB::tRecords::size_type size(editFields.size()), i(0); i < size; ++i) {
    const std::string& type(editFields[i][0]);
    const std::string field(URL::encode(editFields[i][1]));
    const std::string& width(editFields[i][2]);
    const std::string& height(editFields[i][3]);
    const std::string& description(editFields[i][4]);
    const std::string& notes(editFields[i][5]);

    if (type == "edit") {
      const bool qNotes(!notes.empty());
      const std::string value((field == "email" || (field == "userName")) ? "" : rec[i]);

      html << "<tr><th align=\"right\">";

      if (qNotes)
        html << "<a href=\"#A" << i << "\">" << description << "</a></th>";
      else
        html << description << "</th>";

      html << "<td>";

      if ((field != "email") && (field != "userName"))
        html << "<input name=\"pre." << field << "\" value=\"" << value << "\" type=\"hidden\" />";

      if (height == "1")
        html << "<input name=\"" << field << "\" value=\"" << value 
             << "\" type=\"text\" size=\"" << width << "\" />";
      else 
        html << "<textarea cols=\"" << width << "\" rows=\"" << height
             << "\"></textarea>";

      html << "</td></tr>" << std::endl;
    } else if (type == "notes") {
      html << "<tr><td colspan=\"2\">" << description << ' ' << notes << "</td></tr>" << std::endl;
    } else if (type == "chk") {
      html << "<tr><th align=\"right\">" << description << "</th><td>"
           << "<input name=\"chk." << field << "\" size=\"" << width << "\" /></td></tr>"
           << std::endl;
    }
  }
  html << "</table>" << std::endl;

  html << "<hr />" << std::endl;
  html << "An e-mail will be sent to you with a link, that you must click on within"
       << " 24 hours to authenticate it, or your update will be thrown away." << std::endl;
  html << "<hr /><ul>" << std::endl;

  for (InfoDB::tRecords::size_type size(editFields.size()), i(0); i < size; ++i) {
    const std::string& type(editFields[i][0]);
    const std::string& notes(editFields[i][5]);
    if (!notes.empty() && type == "edit") {
      const std::string& description(editFields[i][4]);
      html << "<li id=\"A" << i << "\"><b>" << description << "</b> " << notes 
	   << "</li>" << std::endl;
    }
  }

  html << "</ul>" << std::endl;
  html << "</div>" << std::endl;
  html << "</form>" << std::endl;
  html << "</div>" << std::endl;

  HTTP http(std::cout, &html);
  http.content();
  const time_t now(time(0));
  http.modified(now);
  http.expires(now + 3600);

  return 0;
}
