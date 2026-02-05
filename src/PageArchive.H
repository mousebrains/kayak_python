#ifndef INC_PageArchive_H_
#define INC_PageArchive_H_

#include <string>
#include <iosfwd>

namespace PageArchive {
  time_t lastModified(const std::string& name);

  void dumpPage(const std::string& name,
                const std::string& mimetype,
                const int secondsToExpiry,
                const std::string& content);

  bool spewPage(const std::string& name,
                std::ostream& os);
}

#endif // INC_PageArchive_H_
