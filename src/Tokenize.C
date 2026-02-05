#include <Tokenize.H>
#include <iostream>

void
Tokenize::init(const std::string& str,
               const std::string& delim,
               const bool collapse)
{
  std::string::size_type pos(0);

  for (std::string::size_type index;
       (index = str.find_first_of(delim, pos)) != str.npos;) {
    if (index == 0) {
      if (!collapse)
        mTokens.push_back(std::string());
      pos = collapse ? str.find_first_not_of(delim, index) : (index + 1);
      if ((pos >= str.size()) && !collapse)
        mTokens.push_back(std::string());
    } else {
      mTokens.push_back(str.substr(pos, index - pos));
      pos = collapse ? str.find_first_not_of(delim, index) : (index + 1);
      if ((pos >= str.size()) && !collapse)
        mTokens.push_back(std::string());
    } 
  }

  if (pos < str.size())
    mTokens.push_back(str.substr(pos));
}

Tokenize::const_iterator
Tokenize::find(const std::string& str) const
{
 for (const_iterator it = mTokens.begin(); it != mTokens.end(); ++it)
    if (*it == str)
      return it;

  return mTokens.end();
}

std::string
Tokenize::join(const std::string& str) const
{
  std::string result;

  for (const_iterator it = mTokens.begin(); it != mTokens.end(); ++it) {
    result += (result.empty() ? "" : str) + *it;
  }

  return result;
}

std::ostream&
operator << (std::ostream& os,
             const Tokenize& t)
{
  std::string delim;

  os << '{';
  for (Tokenize::const_iterator it = t.begin(); it != t.end(); ++it) {
    os << delim << *it;
    delim = ", ";
  }
  os << '}'; 
  
  return os;
}
