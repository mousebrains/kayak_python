#ifndef INC_RegExp_H_
#define INC_RegExp_H_

#include <string>
#include <regex.h>

class RegExp {
protected:
  bool        mOkay;
  std::string mError;
  regex_t     mPreg;
  int         mFlags;

private:
  bool compile(const char *expression,
	       int flags);

  RegExp();                           // Not defined
  RegExp(const RegExp&);              // Not defined
  RegExp& operator = (const RegExp&); // Not defined
public:
  explicit RegExp(const char *expression);
  RegExp(const char *expression, int flags);

  virtual ~RegExp();

  bool good() const {return mOkay;}
  const std::string& error() const {return mError;}
  int flags() const { return mFlags; }

  bool operator () (const char *line) const;
  bool operator () (const std::string& line) const {
    return (*this)(line.c_str());
  }
  bool match(const char *line,
	     std::string::size_type& off,
	     std::string::size_type& len) const;
  bool match(const char *line,
	     std::string& matched) const;
};

#endif // INC_RegExp_H_
