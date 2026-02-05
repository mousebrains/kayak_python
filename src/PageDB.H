#ifndef INC_PageDB_H_
#define INC_PageDB_H_

#include <MyDB.H>
#include <Paths.H>

class PageDB {
private:
  MyDB mDB;
  MyQuery& mQuery; // For output

  std::string textQuery(const std::string& page, const std::string& field);
public:
  enum ACTION {
    PAGE,
    FILE,
    PLOT,
    VIEW,
    EDIT,
    SVG,
    PNG,
    UNKNOWN
  };

  static const char * actionName(const ACTION action);
  static ACTION actionName(std::string action);

  class Page {
  private:
    const std::string mName;
    ACTION mAction;
    std::string mMimeType;
    time_t mExpires;
    time_t mModified;
    std::string mBody;
  public:
    Page() : mAction(UNKNOWN), mExpires(-1), mModified(-1) {}

    Page(const std::string& name,
         const ACTION action,
         const std::string& mimeType,
         const time_t expires,
         const time_t modified,
         const std::string& body)
      : mName(name), 
        mAction(action), 
        mMimeType(mimeType), 
        mExpires(expires), 
        mModified(modified), 
        mBody(body) {}
    const std::string& name() const {return mName;}
    ACTION action() const {return mAction;}
    const std::string& mimeType() const {return mMimeType;}
    const time_t expires() const {return mExpires;}
    const time_t modified() const {return mModified;}
    const std::string& body() const {return mBody;}

    void action(const ACTION a) {mAction = a;}
    void mimeType(const std::string& t) {mMimeType = t;}
    void expires(const time_t t) {mExpires = t;}
    void modified(const time_t t) {mModified = t;}
    void body(const std::string& b) {mBody = b;}
  };

  PageDB() : mDB(Paths::MySQLPageDB), mQuery(mDB.query()) {}

  Page operator () (const std::string& name); // Fetch all info

  bool operator () (const std::string& name,
                    const ACTION action,
                    const std::string& mimetype, 
                    const int expires,
                    const std::string& text);

  ACTION action(const std::string& name) {return action(textQuery(name, "page"));}
  std::string body(const std::string& name) {return textQuery(name, "page");}
  std::string mimeType (const std::string& name) {return textQuery(name, "mimetype");}
  time_t modified(const std::string& name);
  time_t expires(const std::string& name);
};

#endif // INC_PageDB_H_
