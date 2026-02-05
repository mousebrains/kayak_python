#include <URLparse.H>
#include <Tokenize.H>
#include <Convert.H>
#include <InfoDB.H>

URLparse::URLparse(InfoDB& info,
                   const std::string& urlCriteria,
                   const std::string& parserCriteria)
{
  MyQuery& query(info.query());

  query << "select URL,parser,hours from URLparse "
	<< " where inActive is null"
        << (urlCriteria.empty() ? "" : (" and URL='" + urlCriteria + "'"))
        << (parserCriteria.empty() ? "" : (" and parser='" + parserCriteria + "'"))
        << ";";

  MyQuery::Result result(query.store());

  for (MyQuery::Result::size_type i = 0; i < result.size(); ++i) {
    MyQuery::Row row(result.at(i));
    mRecords.push_back(Record(row.at(0).c_str(), row.at(1).c_str(),
                              row.at(2).c_str()));
  }
}

bool
URLparse::Record::qHour() const
{
  if (mHours.empty())
    return true;

  if (mHourSet.empty() && mHourExclude.empty()) {
    Tokenize hours(mHours, " ,\t\n", true);
    for (Tokenize::size_type i = 0; i < hours.size(); ++i) {
      const std::string& str(hours[i]);
      if (str.empty())
        continue;
      else if (str[0] == '-') {
        const int hour(Convert::strTo<int>(hours[i]));
        mHourExclude.insert(-hour);
      } else {
        const int hour(Convert::strTo<int>(hours[i]));
        mHourSet.insert(hour);
      }
    }       
  }

  const time_t now(time(0));
  const struct tm *tm(localtime(&now));
   
  return ((mHourExclude.find(tm->tm_hour) == mHourSet.end()) ||
          (mHourSet.find(tm->tm_hour)  != mHourSet.end()));
}

std::ostream&
operator << (std::ostream& os,
             const URLparse& d)
{
  for (URLparse::const_iterator it = d.begin(); it != d.end(); ++it)
    os << "URLparse "<< *it << std::endl;

  return os;
}

std::ostream&
operator << (std::ostream& os,
             const URLparse::Record& r)
{
  os << "{" << r.url() << ", " << r.parser() << ", " << r.hours() << "}";

  return os;
}
