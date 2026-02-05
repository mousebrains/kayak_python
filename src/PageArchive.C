#include <PageArchive.H>
#include <Paths.H>
#include <afstream.H>
#include <fstream>
#include <File.H>
#include <HTTP.H>
#include <CompressString.H>

namespace {
  std::string 
  mkFilename(const std::string& name, 
             const bool qCompress)
  {
    const std::string baseName(File::tail(name)); // For security purposes
    return Paths::PageArchiveRoot + "/" + baseName + (qCompress ? ".gz" : "");
  }

  void doDump(const std::string& name,
              const std::string& mimetype,
              const time_t modified,
              const time_t expires,
              const std::string& content,
              const bool qCompress)
  {
    const std::string fn(mkFilename(name, qCompress));
    {
      const std::string dir(File::dirname(fn));
      File::makedir(dir);
    }
    oafstream os(fn, 0644);
    HTTP http(os);

    http.content(mimetype);
    http.modified(modified);
    http.expires(expires);

    std::string gzContent;

    if (qCompress) {
      http.encoding("gzip");
      Compress::bestCompression();
      gzContent = Compress::string(content);
      http.length(gzContent.size());
    } else 
      http.length(content.size());

    http.end();

    os << (qCompress ? gzContent : content);
  }
}

void
PageArchive::dumpPage(const std::string& name,
                      const std::string& mimetype,
                      const int secondsToExpiry,
                      const std::string& content)
{
  const time_t now(time(0));

  doDump(name, mimetype, now, now + secondsToExpiry, content, false);
  doDump(name, mimetype, now, now + secondsToExpiry, content, true);
}

bool 
PageArchive::spewPage(const std::string& name,
                      std::ostream& os)
{
  const std::string fn(mkFilename(name, HTTP::compressable()));
  std::ifstream is(fn.c_str());

  if (is) {
    char buffer[65536];
    while (is) {
       is.read(buffer, sizeof(buffer));
       const std::streamsize n(is.gcount());
       if (n)
         os.write(buffer, n); 
    }
    return true;
  }
  
  HTTP::errorPage(os, 404, "File not found", "File(" + name + ") not found, " + fn);  

  return false;
}

time_t
PageArchive::lastModified(const std::string& name)
{
  return File::mtime(mkFilename(name, true));
}

