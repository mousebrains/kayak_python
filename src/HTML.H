#ifndef INC_HTML_H_
#define INC_HTML_H_

#include <string>
#include <iostream>
#include <fstream>
#include <sstream>

class HTML {
private:
  std::ostringstream *mOSS;
  std::ofstream *mOSF;
  std::ostream *mOSR;
  std::ostream *mOS;
  bool mqText;
  bool mDumped;
  bool mCompress;
  bool mCompressed;
  bool mHTML;
  bool mHead;
  bool mBody;

  std::string mPage;

  void _compress();

public:
  explicit HTML(bool compress = false, bool qText = false);
  explicit HTML(std::ostream& os, bool compress = false, bool qText = false);
  explicit HTML(const char *filename, bool compress = false, bool qText = false);

  ~HTML();

  bool compress() const {return mOSS && mCompress;}
  bool compressed() const {return mCompressed || compress();}
  bool dump(std::ostream& os);
  bool specialDump(std::ostream& os);
  friend std::ostream& operator << (std::ostream& os, HTML& html);

  std::string::size_type length();

  static const char *HTML401Frameset();
  static const char *HTML401Strict();
  static const char *HTML401Transitional();
  static const char *XHTML10Frameset();
  static const char *XHTML10Strict();
  static const char *XHTML10Transitional();
  static const char *XHTML11();

  template<typename T>
  std::ostream& operator << (const T& t) {return mOS ? (*mOS << t) : *mOS;}

  std::ostream& operator << (std::ostream& (* func)(std::ostream&)) {
    if (mOS)
      func(*mOS);
    return *mOS;
  }

  bool startObject(const std::string& name, const std::string& args = std::string());
  bool endObject(const std::string& name, const std::string& args = std::string()) {
    return !startObject("/" + name, args);
  }

  void startHTML(const std::string& args = std::string()) {
    if (!mqText) 
      mHTML = startObject("html", args);
  }
  void endHTML(const std::string& args = std::string()) {
    if (!mqText) 
      mHTML = endObject("html", args);
  }

  void startHead(const std::string& args = std::string()) {
    if (!mqText) 
      mHead = startObject("head", args);
  }
  void endHead(const std::string& args = std::string()) {
    if (!mqText) 
      mHead = endObject("head", args);
  }

  void startBody(const std::string& args = std::string()) {
    if (!mqText) 
      mBody = startObject("body", args);
  }
  void endBody(const std::string& args = std::string()) {
    if (!mqText) 
      mBody = endObject("body", args);
  }

  static void anchor(std::ostream& os, const std::string& href, const std::string& body);
  void anchor(const std::string& href, const std::string& body);
  void end();
  static void errorPage(std::ostream& os, 
                        const std::string& title, 
                        const std::string& msg);
  void errorPage(const std::string& title, const std::string& msg);
  void head(const std::string& title, const std::string& args = std::string());
  void hr(const std::string& args = std::string()) {startObject("HR", args);}
  void title(const std::string& tit, const std::string& args = std::string());

  void H(const int level,
         const std::string& text, 
         const std::string& args = std::string()) {
    *this << "<h" << level;
    if (!args.empty())
      *this << " " << args;
    *this << ">" << text << "</h" << level << ">" << std::endl;
  }
  void H1(const std::string& t, const std::string& a = std::string()) {return H(1, t, a);}
  void H2(const std::string& t, const std::string& a = std::string()) {return H(2, t, a);}
  void H3(const std::string& t, const std::string& a = std::string()) {return H(3, t, a);}
  void H4(const std::string& t, const std::string& a = std::string()) {return H(4, t, a);}
  void H5(const std::string& t, const std::string& a = std::string()) {return H(5, t, a);}
  void H6(const std::string& t, const std::string& a = std::string()) {return H(6, t, a);}

  class List {
  private:
    HTML& mHTML;
    const std::string mType;
    int nItems;
    std::string mList;
  public:
    List(HTML& html, const std::string& type, const std::string& args = std::string())
      : mHTML(html),
        mType(type),
        nItems(0),
        mList("<" + type + (args.empty() ? std::string("") : (" " + args)) + ">")
    { }
    ~List() {dump();}
    void dump() {
      if (nItems) 
        mHTML << mList << "</" << mType << ">";
    }
    int n() const {return nItems;}
    void item(const std::string& text) { item("", text); }
    void item(const std::string& args,
              const std::string& text) {
      ++nItems;
      mList += "<li";
      if (!args.empty())
        mList += " " + args;
      mList += ">" + text + "\n";
    }
  };

  class UL : public List {
  public:
    UL(HTML& html, const std::string& args = std::string()) : List(html, "UL", args) {}
  };

  class OL : public List {
  public:
    OL(HTML& html, const std::string& args = std::string()) : List(html, "OL", args) {}
  };

  class Table {
  private:
    HTML& mHTML;
    int nRows;
    std::string mTable;
  public:
    Table(HTML& html, const std::string& args = std::string())
      : mHTML(html),
	nRows(0),
	mTable(args.empty() ? "<table>\n" : ("<table " + args + ">\n"))
    { }

    ~Table() { dump(); }

    void dump() {
      if (nRows) 
        mHTML << mTable << "</table>" << std::endl;
    }

    void print(std::ostream& os) const {
      if (nRows)
        os << mTable << "</table>" << std::endl;
    }
   
    class Row {
    private:
      std::string mRow;
    public:
      explicit Row(const std::string& args = std::string()) 
	: mRow(args.empty() ? "<tr>" : "<tr " + args + ">") { }

      void cell(const std::string& key, const std::string& col) {
	mRow += "<" + key + ">" + col;
      }

      const std::string& row() const {return mRow;}

      Row& operator += (const std::string& content) {
	mRow += content;
	return *this;
      }
    };

    void row(const Row& r) {
      ++nRows;
      mTable += r.row() + "\n";
    }

    void row(const std::string& r) {
      ++nRows;
      mTable += r + "\n";
    }

    friend std::ostream& operator << (std::ostream& os, Table& html);
  };
};

#endif // INC_HTML_H_
