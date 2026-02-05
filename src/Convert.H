#ifndef INC_Convert_H_
#define INC_Convert_H_


#include <sstream>
#include <string>
#include <ctime>

namespace Convert {
  template <class T>
  std::string toStr(const T& t) {
    std::ostringstream os;
    os << t;
    return os.str();
  }
 
  template <class T>
  T strTo(const std::string& str) {
    std::istringstream is(str);
    T t;
    is >> t;
    return t;
  }
 
  std::string toString(const time_t time, const std::string& format = "%m/%d/%Y %H:%M %Z");
  std::string toString(const struct tm& tm, const std::string& format = "%m/%d/%Y %H:%M %Z");

  time_t toTime(const std::string& text);
  time_t toTime(const std::string& text, const std::string& timezone);

  bool toTime(const std::string& text, struct tm& tm);
  bool toTime(const std::string& text, const std::string& timezone, struct tm& tm);

  time_t toTimeGMT(const std::string& text);
  bool toTimeGMT(const std::string& text, struct tm& tm);
}

#endif // INC_Convert_H_
