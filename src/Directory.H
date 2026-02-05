#ifndef INC_Directory_H_
#define INC_Directory_H_

#include <string>
#include <list>
#include <iosfwd>
#include <algorithm>
#include <dirent.h>

#include <RegExp.H>

class Directory  {
  bool mOkay;
  DIR *dirp;
  std::string mError;
  Directory();

  bool _init(const char *filename);
  Directory( const Directory&);
  Directory & operator= (  const Directory&);

public:
  explicit Directory(const std::string& dirname)
  { mOkay = _init(dirname.c_str());}
  explicit Directory(const char *dirname)
  { mOkay = _init(dirname);}
  
  ~Directory();
  
  operator bool () const {return mOkay;}
  bool okay() const {return mOkay;}
  const std::string& strerror() const {return mError;}

  std::string read() {
    struct dirent *ptr(readdir(dirp));
    return ptr ? std::string(ptr->d_name) : std::string();
  }

  template <typename I>
  Directory &
  fill( I i )
  {
    if( !dirp )
      return *this;
    
    for (struct dirent *ptr; (ptr = readdir(dirp));) 
      *i++ = std::string(ptr->d_name);

    return *this;
  }

  template <typename I>
  Directory &
  fill( I i, const RegExp& re)
  {
    if( !dirp )
      return *this;
    
    for (struct dirent *ptr; (ptr = readdir(dirp));) 
      if (re(ptr->d_name))
        *i++ = std::string(ptr->d_name);

    return *this;
  }
};

#endif // INC_Directory_H_
