#include <Directory.H>
#include <cerrno>
#include <cstring>
#include <iostream>

bool
Directory::_init(const char *dirname)
{
  dirp = (opendir(dirname));

  if (!dirp) {
    mError = std::strerror(errno);
    return false;
  }
  return true;
}

Directory::~Directory() {
  if( dirp)
    closedir(dirp);
}
