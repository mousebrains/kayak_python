#include <Display.C>
#include <HTTP.C>
#include <CGI.C>
#include <CompressString.C>

int
main (int argc,
      char **argv)
{
  const CGI cgi;
  const std::string hashes(cgi.get("h"));

  if (hashes.empty()) {
    HTTP::errorPage(std::cout, 404, "No hashes supplied", "No hashes supplied");
    return 1;
  }

  Display d("Builder", "no_show is null and db_name is not null", hashes);

  std::ostringstream os;
  d.html("", os, true, false);

  const bool qCompress(HTTP::compressable());
  const std::string str(qCompress ? Compress::string(os.str()) : os.str());
  {
    HTTP http(std::cout, 0);
    http.content();
    http.length(str.size());

    if (qCompress)
      http.encoding("gzip");

    const time_t now(time(0));
    http.modified(now);
    http.expires(now + 3600);
  }
  std::cout << str;

  return 0;
}
