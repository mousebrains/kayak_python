#include <RegExp.H>

#include <iostream>

RegExp::RegExp(const char *expression)
{
  mOkay = compile(expression, REG_EXTENDED | REG_ICASE | REG_NOSUB);
}

RegExp::RegExp(const char *expression,
		     int flags)
{
  mOkay = compile(expression, flags);
}

RegExp::~RegExp()
{
  if (mOkay)
    regfree(&mPreg);
}

bool
RegExp::compile(const char *expression,
		   int flags)
{
  mFlags = flags;
  const int rc(regcomp(&mPreg, expression, flags));
  if (rc) {
    char buffer[1024];
    regerror(rc, &mPreg, buffer, sizeof(buffer));
    mError = std::string("Error parsing '") + expression + "', " + buffer;
    return false;
  }
  return true;
}

bool
RegExp::operator () (const char *line) const
{
  return !regexec(&mPreg, line, 0, 0, 0);
}

bool
RegExp::match(const char *line,
		 std::string::size_type& off,
		 std::string::size_type& len) const
{
  if (mFlags & REG_NOSUB)
    return false;
  const size_t nmatch(1);
  regmatch_t pmatch[nmatch];
  if (!regexec(&mPreg, line, nmatch, pmatch, 0)) {
    int so(pmatch[0].rm_so);
    int eo(pmatch[0].rm_eo);
    if ((so == -1) || (eo == -1) || (so > eo))
      return false;
    off = so;
    len = eo - so;
    return true;
  }
  return false;
}

bool
RegExp::match(const char *line, std::string& matched) const
{
  std::string::size_type off, len;
  if( match(line, off, len) ) {
    matched = std::string(line+off, len);
    return true;
  }
  return false;
}
