#include <Description.H>
#include <InfoDB.H>

Description::Description(InfoDB& info)
{
  MyQuery& query(info.query());

  query << "select columnName,type,prefix,suffix from Description order by sortKey;";

  MyQuery::Result result(query.store());

  for (MyQuery::Result::size_type size(result.size()), i(0); i < size; ++i) {
    MyQuery::Row row(result.at(i));
    mRecords.push_back(Record(row.at(0).c_str(), row.at(1).c_str(),
                              row.at(2).c_str(), row.at(3).c_str()));
  }
}

std::ostream&
operator << (std::ostream& os,
             const Description& d)
{
  for (Description::const_iterator et(d.end()), it(d.begin()); it != et; ++it)
    os << "Descrip "<< *it << std::endl;

  return os;
}

std::ostream&
operator << (std::ostream& os,
             const Description::Record& r)
{
  os << "{" << r.name() << ", " << r.type() << ", " << r.prefix() << ", " << r.suffix() << "}";

  return os;
}
