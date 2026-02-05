#include <Convert.H>
#include <TimeZone.H>
#include <iostream>
#include <cstring>

namespace {
  const char *format[] = {
    "%Y%b%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y%m%d%H%M%S",
    "%Y%m%d%H%M",
    "%Y%m%d%H",
    "%Y%m%d",
    "%m/%d/%Y %H%M%S",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H%M",
    "%m/%d/%Y %H:%M",
    "%m/%d.%H:%M",
    "%m/%d%H:%M",
    "%d%b%Y%H%M%S",
    "%d%b%Y%H%M",
    "%I%M %p PST %a %b %d %Y", // 130 PM PST SAT APR 01 2006
    "%I%M %p PDT %a %b %d %Y", // 130 PM PDT SUN APR 02 2006
    "%Y %a %b%d %H:%M:%S", // 2006 Wed Mar21 12:00
    "%Y %a %b%d %H:%M", // 2006 Wed Mar21 12:00
    "%d %b %Y %H:%M:%S", // 03 APR 2006
    "%d %b %Y %H:%M", // 03 APR 2006
    "%a %b %d, %Y %H:%M", // MONDAY APRIL 3, 2006 00:00
    "%Y-%m-%dT%H:%M:%S-00:00", // 2006-03-26T04:00:00-00:00
    "%Y-%m-%dT%H:%M:%SZ", // 2010-09-24T04:30:00Z
    "%Y-%m-%d", // 2010-09-24
    "%m-%d-%Y", // 9-24-2010
    "%m/%d/%Y", // 9/24/2010
    0};

  bool
  myToTime(const char *text,
	   const char *format,
	   const struct tm& reftm,
	   struct tm& tm)
  {
    memcpy(&tm, &reftm, sizeof(reftm));
    const char *ptr(strptime(text, format, &tm));

#ifdef TPW
    std::cout << "text(" << text << ") format(" << format << ") " << (ptr ? true : false)
              << " '" << (ptr ? ptr : "(nill)") << "' " << Convert::toString(tm)
              << ' ' << tm.tm_gmtoff << " " << tm.tm_zone
              << std::endl;
#endif // TPW

    return ptr ? (*ptr == 0) : false;
  }

  bool
  myToTime(const std::string& text,
	   const struct tm& reftm,
	   struct tm& tm)
  {
    const char *str(text.c_str());

    for (int i = 0; format[i]; ++i) {
      if (myToTime(str, format[i], reftm, tm))
	return true;
    }
    return false;
  }
}

namespace Convert {
  time_t
  toTime(const std::string& text, 
         const std::string& timezone)
  {
    if (timezone.empty()) 
      return toTime(text);

    const TimeZone tz(timezone);
    return toTime(text);
  }

  time_t
  toTime(const std::string& text)
  {
    struct tm tm;
    if (toTime(text, tm))
      return mktime(&tm);
    return -1;
  }

  bool
  toTime(const std::string& text,
	 struct tm& tm)
  {
    struct tm ltm;
    const time_t now(time(0));
    localtime_r(&now, &ltm);
    ltm.tm_sec = 0; // Zero out the seconds
    return myToTime(text, ltm, tm);
  }

  time_t
  toTimeGMT(const std::string& text)
  {
    struct tm tm;
    if (toTimeGMT(text, tm))
      return mktime(&tm);
    return -1;
  }

  bool
  toTimeGMT(const std::string& text,
	      struct tm& tm)
  {
    struct tm gmt;
    const time_t now(time(0));
    gmtime_r(&now, &gmt);
    gmt.tm_sec = 0;
    return myToTime(text, gmt, tm);
  }

  std::string
  toString(const time_t time,
           const std::string& format)
  {
    struct tm tm;
    localtime_r(&time, &tm);
    return toString(tm, format);
  }

  std::string
  toString(const struct tm& tm,
           const std::string& format)
  {
    char buffer[1024];
    strftime(buffer, sizeof(buffer) - 1, format.c_str(), &tm);
    return std::string(buffer);
  }
}
