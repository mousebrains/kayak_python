#ifndef INC_CMD_H_
#define INC_CMD_H_

#include <string>

class CGI;

namespace CMD {
  bool badHash(const std::string& hash, const std::string& id = std::string());
  int description(const std::string& hash);
  int edit(const std::string& hash); 
  int file(const std::string& key);
  bool loadMaster(const std::string& hash, std::string& displayName, std::string& dbName);
  int page(const std::string& name);
  int plot(const CGI& cgi, 
           const std::string& id, 
	   const std::string& hash,
	   const std::string& type,
	   const std::string& label);
  int view(const CGI& cgi, const std::string& hash); 
}

#endif // INC_CMD_H_
