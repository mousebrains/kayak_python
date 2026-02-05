#include <HTTP.H>
#include <HTML.H>
#include <File.H>
#include <fstream>
#include <sstream>
#include <cstdlib>
#include <cstring>

HTTP::HTTP(HTML *page)
  : mOSS(new std::ostringstream),
    mOSF(0),
    mOS(mOSS),
    mDumped(false),
    mEnd(false),
    mHTML(page)
{
}

HTTP::HTTP(std::ostream& os, HTML *page)
  : mOSS(0),
    mOSF(0),
    mOS(&os),
    mDumped(false),
    mEnd(false),
    mHTML(page)
{
}

HTTP::HTTP(const char *filename, HTML *page)
  : mOSS(0),
    mOSF(new std::ofstream(filename)),
    mOS(mOSF),
    mDumped(false),
    mEnd(false),
    mHTML(page)
{
}

HTTP::~HTTP()
{
  if (!mDumped && (mOSS || (mOS && mHTML)))
    dump(*mOS);
  else if (!mEnd)
    *mOS << std::endl;

  delete mOSS;
  delete mOSF;
}

void
HTTP::dump(std::ostream& os, HTML *page)
{
  if (&os == mOS)
    mDumped = true;

  if (page) {
    length(page->length());
    if (page->compressed())
      encoding("gzip");
  }
    
  if (mOSS) {
    // *mOSS << std::ends; // Legacy
    os << mOSS->str();
  }

  if (!mEnd)
    os << std::endl;

  if (page)
    os << *page;
}

std::string
HTTP::date(const time_t& when) 
{
  struct tm *gmt(gmtime(&when));
  char buffer[256];
  strftime(buffer, sizeof(buffer), "%A, %d %b %Y %H:%M:%S GMT", gmt);
  return buffer;
}

void 
HTTP::content (const char *type) 
{
  *this << "Content-Type: " << type << std::endl;
}

void 
HTTP::content (const std::string& type) 
{
  *this << "Content-Type: " << type << std::endl;
}

void 
HTTP::expires (const time_t& when) 
{
  *this << "Expires: " << date(when) << std::endl;
}

void 
HTTP::modified (const time_t& when) 
{
  *this << "Last-Modified: " << date(when) << std::endl;
}

void 
HTTP::encoding(const std::string& encoding) 
{
  *this << "Content-Encoding: " << encoding << std::endl;
}

void 
HTTP::length(const size_t length) 
{
  *this << "Content-Length: " << length << std::endl;
}

void 
HTTP::setCookie(const std::string& cookie) 
{
  *this << "Set-Cookie: " << cookie << std::endl;
}

void 
HTTP::location(const std::string& url) 
{
  *this << "Location: " << url << std::endl;
}

void 
HTTP::noCache()
{
  *this << "Pragma: no-cache" << std::endl
        << "Cache-control: no-cache" << std::endl;
  expires(time_t(0.));
}

void 
HTTP::end()
{
  if (mOS) {
    *mOS << std::endl; 
    mEnd = true;
  }
}

void 
HTTP::refresh(const int seconds) 
{
  *this << "Refresh: " << seconds << std::endl;
}

void 
HTTP::status(const int code) 
{
  *this << "Status:" << code << std::endl;
}

void
HTTP::errorPage(int code,
                const std::string& title,
                const std::string& body)
{
  if (mOS)
    errorPage(*mOS, code, title, body);
}


void
HTTP::errorPage(std::ostream& os,
                int code,
                const std::string& title,
                const std::string& body)
{
  HTML page;
  page.errorPage(title, body);

  HTTP h(os, &page);

  h.content("text/html");
  h.status(code);
}

const char *
HTTP::encodings()
{
  return getenv("HTTP_ACCEPT_ENCODING");
}

bool
HTTP::encodings(const char *type)
{
  const char *ptr(encodings());
  if (!ptr)
    return false;
  return strstr(ptr, type) || strstr(ptr, "*");
}

bool
HTTP::encodings(const std::string& type)
{
  return encodings(type.c_str());
}

bool
HTTP::compressable()
{
  return encodings("gzip");
}

bool 
HTTP::dumpFile(std::ostream& os,
               const std::string& filename,
               const std::string& contentType,
               const std::string& encoding,
               const time_t& timeout)
{
 File file(filename);

  if (!file)
    return false;

  std::ifstream is(filename.c_str());

  if (!is)
    return false;

  {
    HTTP HTTP;

    if (!contentType.empty())
      HTTP.content(contentType);

    const time_t mtime(file.mtime());
    HTTP.modified(mtime);

    if (timeout != time_t(0)) {
      const time_t expires(mtime + timeout);
      const time_t now(time(0));
      HTTP.expires((expires > now) ? expires : (now + 60));
    }

    if (!encoding.empty())
      HTTP.encoding(encoding);

    HTTP.length(file.size());

    HTTP.dump(os);
  }

  char buffer[65536];

  while (is.read(buffer, sizeof(buffer))) 
    os.write(buffer, is.gcount());

  if (is.gcount())        // Read EOF during the read
    os.write(buffer, is.gcount());

  return true;
}
