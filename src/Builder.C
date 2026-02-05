#include <Builder.H>
#include <InfoDB.H>

Builder::Builder(InfoDB& info,
                 const std::string& builderDB)
{
  MyQuery& query(info.query());

  query << "select qUse,type,field,length,nameText,nameHTML from "
        << builderDB << " order by sortKey;";

  const MyQuery::Result result(query.store());

  for (MyQuery::Result::size_type size(result.size()), i(0); i < size; ++i) {
    const MyQuery::Row row(result.at(i));
    const std::string use(row.at(0).c_str());
    const std::string type(row.at(1).c_str());
    const std::string field(row.at(2).c_str());
    const size_t length(row.at(3));
    const std::string text(row.at(4).c_str());
    const std::string html(row.at(5).c_str());
    const Record record(use, type, field, length, text, html);
    mRecords.push_back(Record(use, type, field, length, text, html));
  }
}

Builder::Record::Record(const std::string& use,
                        const std::string& type,
                        const std::string& field,
                        const size_t length,
                        const std::string& text,
                        const std::string& html)
  : mUse(use),
    mType(type),
    mField(field),
    mLength(length),
    mText(text),
    mHTML(html),
    mColUsed(false)
{
}

std::ostream&
operator << (std::ostream& os,
             const Builder& d)
{
  for (Builder::const_iterator et(d.end()), it(d.begin()); it != et; ++it)
    os << "Builder "<< *it << std::endl;

  return os;
}

std::ostream&
operator << (std::ostream& os,
             const Builder::Record& r)
{
  os << "{" << r.use() 
     << ", " << r.type() 
     << ", " << r.field() 
     << ", " << r.length() 
     << ", " << r.text() 
     << ", " << r.html()
     << "}";

  return os;
}
