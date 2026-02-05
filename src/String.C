#include <String.H>
#include <iostream>

std::string& 
String::replaceInPlace(std::string& str, 
                       const std::string& from, 
                       const std::string& to)
{
  for (std::string::size_type pos = 0; pos != str.npos;) {
    const std::string::size_type index(str.find(from, pos));
    if (index == str.npos)
      return str;
    str.replace(index, from.size(), to);
    pos = index + to.size(); 
  }
  return str;
}

std::string
String::replace(const std::string& str,
                const std::string& from,
                const std::string& to)
{
  std::string a(str);

  return replaceInPlace(a, from, to);
}

std::string&
String::toLowerInPlace(std::string& str)
{
  for (std::string::size_type i = 0; i < str.size(); ++i)
    str[i] = std::tolower(str[i]);

  return str;
}

std::string
String::toLower(const std::string& str)
{
  std::string a(str);
  return toLowerInPlace(a);
}

std::string&
String::toUpperInPlace(std::string& text)
{
  for (std::string::size_type i = 0; i < text.size(); ++i) 
    text[i] = toupper(text[i]);

  return text;
}

std::string
String::toUpper(const std::string& text)
{
  std::string str(text);
  return toUpperInPlace(str);
}

std::string&
String::trimInPlace(std::string& str, 
                    const std::string& whitespace)
{
  const std::string::size_type s(str.find_first_not_of(whitespace));

  if (s == str.npos) {
    str.clear();
    return str;
  }

  const std::string::size_type l(str.find_last_not_of(whitespace));
  str = str.substr(s, l - s + 1);

  return str;
}

std::string
String::trim(const std::string& str,
             const std::string& whitespace)
{
  std::string a(str);
  return trimInPlace(a, whitespace);
}

std::string&
String::collapseInPlace(std::string& str, 
                        const std::string& replaceWith,
                        const std::string& whitespace)
{
  for (std::string::size_type pos = 0, index;
       (index = str.find_first_of(whitespace, pos)) != str.npos;) {
    const std::string::size_type n(str.find_first_not_of(whitespace, index));
    if (n == str.npos) { // Rest of string is white
      str.replace(index, str.size() - index, replaceWith);
      break;
    }
    str.replace(index, n - index, replaceWith);
    pos = index + replaceWith.size();
  }
  return str;
}

std::string
String::collapse(const std::string& str,
                 const std::string& replaceWith,
                 const std::string& whitespace)
{
  std::string a(str);
  return collapseInPlace(a, replaceWith, whitespace);
}
