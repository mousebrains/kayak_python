#include <CMD.H>
#include <MakeDescription.H>
#include <HTTP.H>
#include <CompressString.H>

int 
CMD::description(const std::string& hash) 
{
  std::ostringstream os;
  MakeDescription d(os, true, true, true);
  d.master(hash, std::string());
  d.close(hash);

  const bool qCompress(HTTP::compressable());
  const std::string str(qCompress ? Compress::string(os.str()) : os.str());

  { // Force http to be deconstructed before std::cout << str is called 
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
