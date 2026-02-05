#ifndef INC_TimeZone_H_
#define INC_TimeZone_H_

#include <string>

class TimeZone {
private:
  const char *mOldPtr;
  const std::string mOldTZ;
  std::string mCurrentTZ;
public:
  TimeZone(const std::string& tz);
  TimeZone();

  ~TimeZone();

  void operator () (const std::string& tz);

  const std::string& operator () () const {return mCurrentTZ;}
};

#endif // INC_TimeZone_H_
