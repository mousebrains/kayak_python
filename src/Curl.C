#include <Curl.H>
#include <iostream>
#include <fstream>
#include <cerrno>
#include <cstring>
#include <cstdlib>

namespace {
  CURL *curlHandle(0);

  CURL *getHandle() {
    if (!curlHandle) {
      curl_global_init(CURL_GLOBAL_ALL);
      if (!(curlHandle = curl_easy_init())) {
        std::cerr << "Error constructing a curl handle" << std::endl;
        exit(1);
      }
    }
    return curlHandle;
  }
}

Curl::Curl(const std::string& url)
  : mStatus(CURLE_FAILED_INIT),
    mResponseCode(0),
    mURL(url)
{
  CURL *handle(getHandle());

  curl_easy_setopt(handle, CURLOPT_URL, url.c_str());
  curl_easy_setopt(handle, CURLOPT_WRITEFUNCTION, callback);
  curl_easy_setopt(handle, CURLOPT_WRITEDATA, (void *) this);
  curl_easy_setopt(handle, CURLOPT_USERAGENT, "libcurl-agent/1.0");
  curl_easy_setopt(handle, CURLOPT_SSL_VERIFYPEER, 0);
  curl_easy_setopt(handle, CURLOPT_NOSIGNAL, 1); // Disable abort signals
  curl_easy_setopt(handle, CURLOPT_TIMEOUT, 5 * 60); // 5 minute time outs
  mStatus = curl_easy_perform(handle);

  if (mStatus == CURLE_OK) {
    curl_easy_getinfo(handle, CURLINFO_RESPONSE_CODE, &mResponseCode);
    char *ptr;
    if ((curl_easy_getinfo(handle, CURLINFO_CONTENT_TYPE, &ptr) == CURLE_OK) && ptr) 
      mContentType = ptr;
    else
      std::cerr << "Got '" << url << "', but could not get info, "
	        << curl_easy_strerror(mStatus) << std::endl;
  } else {
    std::cerr << "Failed to get '" << url << "', "
	      << curl_easy_strerror(mStatus) << std::endl;
  }
}

void
Curl::wrapup()
{
  if (curlHandle) {
    curl_easy_cleanup(curlHandle);
    curlHandle = 0;
  }
}

size_t
Curl::callback(void *ptr,
		 size_t size,
		 size_t nmemb,
		 void *data)
{
  const size_t realsize(size * nmemb);
  Curl *cb((Curl *) data);
  cb->mText.append((const char *) ptr, realsize);
  return realsize;
}

bool
Curl::writeFile(const std::string& filename) const
{
  std::ofstream os(filename.c_str());

  if (!os) {
    std::cerr << "Error opening '" << filename << "', " << strerror(errno) << std::endl;
    return false;
  }

  os << mText;

  return true;
}

std::ostream&
operator << (std::ostream& os, 
             const Curl& c)
{
  os << c.url() << std::endl;
  os << c.str();

  return os;
}
