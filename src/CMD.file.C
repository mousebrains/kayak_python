#include <CMD.H>
#include <PageDB.H>
#include <File.H>
#include <HTTP.H>
#include <ReadFile.H>
#include <CompressString.H>


int 
CMD::file(const std::string& key) 
{
  PageDB db;
  PageDB::Page page(db(key));

  const std::string fn(page.body());

  if (!File::exists(fn)) {
    HTTP::errorPage(std::cout, 404, "Page not found", "Page not found, '" + key + "'");
    return 1;
  }

  page.body(ReadFile(fn));

  if (page.body().empty()) {
    HTTP::errorPage(std::cout, 404, "Page empty", "Page empty, '" + key + "'");
    return 1;
  }

  page.modified(File::mtime(fn));

  if (page.mimeType().empty()) {
    HTTP::errorPage(std::cout, 404, "Empty mime type for " + page.name(), 
                    "No mime type found for (" + page.name() + ")");
    return 1;
  }

  if (page.body().empty()) {
    std::cerr << "Empty body found for (" << page.name() << ")" << std::endl;
    HTTP::errorPage(std::cout, 404, "Empty body for " + page.name(), 
                    "No body found for (" + page.name() + ")");
    return 1;
  }

  if ((page.action() != PageDB::PAGE) && (page.action() != PageDB::FILE)) {
    HTTP::errorPage(std::cout, 404, "Invalid action",
                    "Invalid action found for (" + page.name() + ")");
    return 1;
  }

  if (page.modified() == -1)
    page.modified(time(0));
 
  if (page.expires() == -1)
    page.expires(time(0));

  HTTP http(std::cout);

  const bool qCompressable(http.compressable()); 
  const std::string& body(page.body());
  const std::string gzBody(qCompressable ? Compress::string(body) : std::string());

  http.content(page.mimeType());
  http.modified(page.modified());
  http.expires(page.expires());

  if (qCompressable) {
    http.encoding("gzip");
    http.length(gzBody.size());
  } else
    http.length(page.body().size());

  http.end();

  std::cout << (qCompressable ? gzBody : body);

  return 0;
}
