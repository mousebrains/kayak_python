#ifndef INC_Tokenize_H_
#define INC_Tokenize_H_

#include <MyDB.H>
#include <string>
#include <iosfwd>

class Tokenize {
public:
  typedef std::vector<std::string> tTokens;
  typedef tTokens::iterator iterator;
  typedef tTokens::const_iterator const_iterator;
  typedef tTokens::const_reverse_iterator const_reverse_iterator;
  typedef tTokens::size_type size_type;
private:
  tTokens mTokens;
  void init(const std::string& str, const std::string& delim, const bool collapse);
public:
  Tokenize(const char * str, 
           const std::string& delim=" \t\n", 
           const bool collapse = true) {
    if (str)
      init(str, delim, collapse);
  }

  Tokenize(const std::string& str, 
           const std::string& delim=" \t\n", 
           const bool collapse = true) {
    init(str, delim, collapse);
  }

  Tokenize(const MyQuery::ColData& cd, 
           const std::string& delim=" \t\n", 
           const bool collapse = true) {
    init(cd.c_str(), delim, collapse);
  }


  void tokenize(const std::string& str, 
                const std::string& delim=" \t\n", 
                const bool collapse = true) {
    mTokens.clear();
    init(str, delim, collapse);
  }

  size_type size() const {return mTokens.size();}
  bool empty() const {return mTokens.empty();}

  const_iterator begin() const {return mTokens.begin();}
  const_iterator end() const {return mTokens.end();}

  iterator begin() {return mTokens.begin();}
  iterator end() {return mTokens.end();}

  const_reverse_iterator rbegin() const {return mTokens.rbegin();}
  const_reverse_iterator rend() const {return mTokens.rend();}

  const_iterator find(const std::string& str) const;

  const std::string& operator [] (const tTokens::size_type i) const {return mTokens[i];}
  std::string& operator [] (const tTokens::size_type i) {return mTokens[i];}

  const tTokens& tokens() const {return mTokens;}

  std::string join(const std::string& str) const;
};

std::ostream& operator << (std::ostream& os, const Tokenize& t);

#endif // INC_Tokenize_H_
