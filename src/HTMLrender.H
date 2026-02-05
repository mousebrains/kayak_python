#ifndef INC_HTMLrender_H_
#define INC_HTMLrender_H_

#include <string>
#include <map>
#include <vector>

typedef unsigned char xmlChar;

class HTMLrender {
private:
  enum tAction {
    SKIP = 0,
    RECORD,
    NEWLINE,
    DENEWLINE,
    BAR
  } mAction;

  typedef std::vector<tAction> tStack;
  tStack mStack;
  
  typedef std::map<std::string, tAction> tActions;
  tActions mActions;
  
  std::string mText;

  static void startElement(void *ptr, const xmlChar *name, const xmlChar **attributes) {
    static_cast<HTMLrender *>(ptr)->startElement(name, attributes);
  }
  
  static void endElement(void *ptr, const xmlChar *name) {
    static_cast<HTMLrender *>(ptr)->endElement(name);
  }
  
  static void characters(void *ptr, const xmlChar *name, int length) {
    static_cast<HTMLrender *>(ptr)->characters(name, length);
  }
  
  void startElement(const xmlChar *name, const xmlChar **attributes);
  void endElement(const xmlChar *name);
  void characters(const xmlChar *name, int length);
public:
  HTMLrender(const std::string& page);
  const std::string& str() const {return mText;}
};

#endif // INC_HTMLrender_H__
