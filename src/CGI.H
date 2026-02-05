#ifndef INC_CGI_H_
#define INC_CGI_H_

#include <Tokenize.H>
#include <string>
#include <vector>
#include <map>
#include <iosfwd>

class CGI {
public:
  typedef std::map<std::string, std::string> tVars;

private:
  Tokenize mPath;

  tVars mVars;

public:
  CGI();

  const std::string& operator [] (const Tokenize::size_type index) const {return mPath[index];}
  const std::string& getPath(const Tokenize::size_type offset) const {return mPath[offset];}
  bool getPath(Tokenize::size_type offset, std::string& value) const;
  Tokenize::size_type nPath() const {return mPath.size();}

  bool isSet(const std::string& key) const {
    return mVars.find(key) != mVars.end();
  }

  std::string get(const std::string& key) const {
    std::string value;
    getVar(key, value);
    return value;
  }

  std::string operator () (const std::string& key) const { return get(key); }

  std::string get(const std::string& key,
                  const std::string& def) const {
    std::string value;
    return getVar(key, value) ? value : def;
  }

  bool getVar(const std::string& key, std::string& value) const;

  typedef tVars::const_iterator const_iterator;

  const_iterator begin() const {return mVars.begin();}
  const_iterator end() const {return mVars.end();}
  const_iterator find(const std::string& key) const {return mVars.find(key);}

  typedef tVars::size_type size_type;
  size_type size() const {return mVars.size();}

  bool empty() const {return mVars.empty();}

  friend std::ostream& operator << (std::ostream& os, const CGI& c);

  static std::string URL();
};

#endif // INC_CGI_H_
