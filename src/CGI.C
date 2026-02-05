#include <CGI.H>
#include <URL.H>
#include <Convert.H>
#include <Tokenize.H>
#include <Env.H>
#include <list>
#include <iostream>
#include <algorithm>

CGI::CGI()
  : mPath(getenv("PATH_INFO"), "/")
{
  const char *reqMethod(getenv("REQUEST_METHOD"));
  if (reqMethod) {
    std::string input;
    if (!strcasecmp(reqMethod, "POST")) { // 0 => exact match
      const char *len(getenv("CONTENT_LENGTH"));
      if (len) {
        const std::string::size_type n(Convert::strTo<std::string::size_type>(len));
        char *buffer = new char[n + 1];
        std::cin.get(buffer, n + 1, '\0');
        input = buffer;
        delete [] buffer;
      }
    } else {
      const char *queryString(getenv("QUERY_STRING"));
      if (queryString)
        input = queryString;
    }
    Tokenize vars(input, "&");
    for (Tokenize::const_iterator et(vars.end()), it(vars.begin()); it != et; ++it) {
      std::string::size_type n(it->find('='));
      if (n != it->npos) 
        mVars[URL::decode(it->substr(0, n))] = URL::decode(it->substr(n + 1, it->npos));
      else 
        mVars[URL::decode(*it)] = "";
    }

  }

  std::for_each(mPath.begin(), mPath.end(), URL::decode);
}

bool
CGI::getPath(Tokenize::size_type offset,
             std::string& result) const
{
  const Tokenize::size_type len(mPath.size());

  if (offset >= len)
    return false;

  
  if (offset >= (len / 2)) {
    offset = (len - 1) - offset;
    for (Tokenize::const_reverse_iterator et(mPath.rend()), it(mPath.rbegin()); it != et; ++it) {
      if (!offset) {
        result = *it;
        return true;
      }
      --offset;
    }
  } else {
    for (Tokenize::const_iterator et(mPath.end()), it(mPath.begin()); it != et; ++it) {
      if (!offset) {
        result = *it;
        return true;
      }
      --offset;
    }
  }
  return false;
}

bool
CGI::getVar(const std::string& key,
            std::string& value) const
{
  tVars::const_iterator it(mVars.find(key));
  if (it == mVars.end())
    return false;
  value = it->second;
  return true;
}

std::ostream&
operator << (std::ostream& os,
             const CGI& c)
{
  os << "Path(" << c.mPath.size() << "):" << std::endl;
  for (Tokenize::const_iterator et(c.mPath.end()), it(c.mPath.begin()); it != et; ++it)
    os << " '" << *it << "'";
  os << std::endl;

  os << "Vars(" << c.mVars.size() << "):" << std::endl;
  for (CGI::tVars::const_iterator et(c.mVars.end()), it(c.mVars.begin()); it != et; ++it)
    os << "  '" << it->first << "' -> '" << it->second << "'" << std::endl;

  return os;
}

std::string
CGI::URL()
{
  const char *script(Env::get("SCRIPT_NAME"));

  if (script)
    return std::string(script);

  const char *URI(Env::get("REQUEST_URI"));
  
  return URI ? std::string(URI) : std::string();
}
