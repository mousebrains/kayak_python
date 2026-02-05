#ifndef INC_CompressString_H_
#define INC_CompressString_H_

#include <iosfwd>
#include <string>

namespace Compress {
  size_t stream(std::ostream& os, const char *source);
  size_t stream(std::ostream& os, const char *source, const size_t length);
  size_t stream(std::ostream& os, const std::string& source);

  std::string string(const char *source);
  std::string string(const char *source, const size_t length);
  std::string string(const std::string& source);

  void bestSpeed();
  void bestCompression();
  void level(const int level);
}

#endif // INC_CompressString_H_
