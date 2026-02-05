#ifndef INC_Paths_H_
#define INC_Paths_H_

#include <string>

namespace Paths {
  extern const std::string MaintainerName; // Who to contact for these pages
  extern const std::string Maintainer; // Who to contact for these pages

  extern const std::string PageArchiveRoot; // Where prepared pages are stored

  extern const std::string URL; // Base URL (http://levels.wkcc.org)

  extern const std::string DocumentRoot; // Base URL (/levels/)
  extern const std::string CGIRoot; // Base URL for cgi scripts
  extern const std::string JSRoot; // Base URL for js scripts

  extern const std::string MySQLUser;   // Login username for mysql queries
  extern const std::string MySQLPasswd; // password for mysql queries
  extern const std::string MySQLHost;   // host for mysql queries
  extern const std::string MySQLInfoDB; // Database name for basic information
  extern const std::string MySQLPageDB; // Database name for prepared pages
  extern const std::string MySQLDataDB; // Database name for harvested data
}

#endif // INC_Paths_H_
