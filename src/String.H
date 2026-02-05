#ifndef INC_String_H_
#define INC_String_H_

#include <string>

namespace String {
  std::string& collapseInPlace(std::string& str, 
                               const std::string& replaceWith = " ",
                               const std::string& whitespace=" \t\n");
  std::string collapse(const std::string& str,
                       const std::string& replaceWith = " ",
                       const std::string& whitespace=" \t\n");

  std::string& trimInPlace(std::string& str, const std::string& whitespace=" \t\n");
  std::string trim(const std::string& str, const std::string& whitespace=" \t\n");
  
  std::string& replaceInPlace(std::string& str, const std::string& from, const std::string& to);
  std::string replace(const std::string& str, const std::string& from, const std::string& to);

  std::string& toLowerInPlace(std::string& str);
  std::string toLower(const std::string& str);

  std::string& toUpperInPlace(std::string& str);
  std::string toUpper(const std::string& str);
}

#endif // INC_String_H_
