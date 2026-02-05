#ifndef INC_Curl_H_
#define INC_Curl_H_

#include <curl/curl.h>
#include <string>
#include <iosfwd>

class Curl {
private:
  CURLcode mStatus;
  long mResponseCode;
  std::string mContentType;
  std::string mURL;
  std::string mText;
  
  static size_t callback(void *ptr, size_t size, size_t nmemb, void *data);
public:
  Curl(const std::string& URL);
  operator bool () const {return mStatus == CURLE_OK;}
  long responseCode() const {return mResponseCode;}
  const std::string& contentType() const {return mContentType;} 
  const std::string& str() const {return mText;}
  const std::string& url() const {return mURL;}
  bool writeFile(const std::string& filename) const;

  static void wrapup();
};

std::ostream& operator << (std::ostream& os, const Curl& c);

#endif // INC_Curl_H_
