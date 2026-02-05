#include <TimeZone.H>
#include <Env.H>
#include <String.H>
#include <map>

namespace {
  std::string remap(const std::string& tz) {
    typedef std::map<std::string, std::string> tMap;
    static tMap remap;

    if (remap.empty()) {
      remap.insert(std::make_pair("EDT", "US/Eastern"));
      remap.insert(std::make_pair("EST", "US/Eastern"));
      remap.insert(std::make_pair("CDT", "US/Central"));
      remap.insert(std::make_pair("CST", "US/Central"));
      remap.insert(std::make_pair("MDT", "US/Mountain"));
      remap.insert(std::make_pair("MST", "US/Mountain"));
      remap.insert(std::make_pair("PDT", "US/Pacific"));
      remap.insert(std::make_pair("PST", "US/Pacific"));
    }
    tMap::const_iterator it(remap.find(String::toUpper(tz)));
    return it == remap.end() ? tz : it->second; 
  }
}


TimeZone::TimeZone()
  : mOldPtr(Env::get("TZ")),
    mOldTZ(mOldPtr ? mOldPtr : std::string()),
    mCurrentTZ(mOldTZ)
{
}

TimeZone::TimeZone(const std::string& tz)
  : mOldPtr(Env::get("TZ")),
    mOldTZ(mOldPtr ? mOldPtr : std::string()),
    mCurrentTZ(remap(tz))
{
  if (!mCurrentTZ.empty() && (mCurrentTZ != mOldTZ)) {
    Env::put("TZ", mCurrentTZ);
    tzset();
  }
}

void 
TimeZone::operator () (const std::string& tz) 
{
  const std::string timeZone(remap(tz));

  if (timeZone != mCurrentTZ) {
    if (timeZone.empty() || (mOldTZ == timeZone)) {
      if (mOldTZ.empty()) 
        Env::unset("TZ");
      else 
        Env::put("TZ", mOldTZ);
      tzset();
      mCurrentTZ = mOldTZ;
    } else {
      mCurrentTZ = timeZone;
      Env::put("TZ", timeZone);
      tzset();
    }
  }
}

TimeZone::~TimeZone() { 
  (*this)(mOldTZ); 
}
