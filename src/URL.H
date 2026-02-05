#ifndef INC_URL_H_
#define INC_URL_H_

#include <string>

namespace URL {
  std::string encode(const std::string& src);
  std::string decode(const std::string& src);
}

#endif // INC_URL_H_
