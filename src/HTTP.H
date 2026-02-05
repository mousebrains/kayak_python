#ifndef INC_HTTP_H_
#define INC_HTTP_H_

#include <string>
#include <iostream>
#include <fstream>
#include <sstream>

class HTML;
class EtsTimeMs;

class HTTP {
private:
  std::ostringstream *mOSS;
  std::ofstream *mOSF;
  std::ostream *mOS;
  bool mDumped;
  bool mEnd;
  HTML *mHTML;

public:
  HTTP(HTML *page = 0);
  HTTP(std::ostream& os, HTML *page = 0);
  HTTP(const char *filename, HTML *page = 0);

  ~HTTP();

  void dump(std::ostream& os) {dump(os, mHTML);}
  void dump(std::ostream& os, HTML *page);
  void dump(HTML *page) {if (mOS) dump(*mOS, page);}

  template<typename T>
  std::ostream& operator << (const T& t) {
    return mOS ? (*mOS << t) : *mOS;
  }

  std::string date(const time_t& when);
  void content (const char *type = "text/html");
  void content (const std::string& type);
  void encoding(const std::string& encoding);
  void end();
  void expires (const time_t& when);
  void length(const size_t length);
  void location(const std::string& url);
  void modified (const time_t& when);
  void noCache();
  void refresh (const int seconds);
  void setCookie(const std::string& cookie);
  void status(const int length);

  static void errorPage(std::ostream& os,
                        int code, 
                        const std::string& title, 
                        const std::string& body);
  void errorPage(int code, 
                 const std::string& title, 
                 const std::string& body);

  static const char *encodings();
  static bool encodings(const char *type);
  static bool encodings(const std::string& type);
  static bool compressable();

  static bool dumpFile(std::ostream& os,
                       const std::string& filename,
                       const std::string& contentType,
                       const std::string& encoding,
                       const time_t& timeout);
};

#endif // INC_HTTP_H_
