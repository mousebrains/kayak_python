#ifndef INC_MakeDescription_H_
#define INC_MakeDescription_H_

#include <InfoDB.H>
#include <Description.H>
#include <DataDB.H>
#include <iosfwd>
#include <string>

class MakeDescription {
private:
  InfoDB mInfo;
  Description mDescription;
  DataDB mData;

  std::ostream& mOS;
  const bool mEdit;
  const bool mFullPage;
  std::string mTail;
  std::string mFields;

public:
  MakeDescription(std::ostream& os,
                  const bool fullPage,
                  const bool editPage,
                  const bool pointToFullPage);

  ~MakeDescription() {close(std::string());}

  bool close(const std::string& hash);

  const std::string& fields() const {return mFields;}

  // bool master(const std::string& criteria = std::string());
  bool master(const std::string& hash, const std::string& criteria);

  bool spew(const InfoDB::tRecords& records);
  bool spew(const InfoDB::tRecord& record);
};

#endif // INC_MakeDescription_H_
