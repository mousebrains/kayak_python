#include <afstream.H>

#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <sys/stat.h>

oafstream::oafstream(const std::string& filename)
  : std::ostream(0)
{
  setup(filename, filename);
}

oafstream::oafstream(const std::string& filename,
                     const mode_t permissions)
  : std::ostream(0)
{
  setup(filename, filename, permissions);
}

oafstream::oafstream(const std::string& filename,
                     const std::string& prefix)
  : std::ostream(0)
{
  setup(filename, prefix);
}

oafstream::oafstream(const std::string& filename,
                     const std::string& prefix,
                     const mode_t permissions)
  : std::ostream(0)
{
  setup(filename, prefix, permissions);
}

bool
oafstream::setup(const std::string& filename,
                 const std::string& prefix,
                 const mode_t permissions)
{
  if (!setup(filename, prefix))
    return false;

  if (fchmod(mBuf.fd(), permissions)) {
    std::cerr << "Error setting permissions, " << strerror(errno) << std::endl;
    return false;
  }
  return true;
}

bool
oafstream::setup(const std::string& filename,
                 const std::string& prefix)
{
  mTempname = strdup((prefix + ".XXXXXX").c_str());

  if (!mTempname) {
    std::cerr << "Error allocating space for " << filename 
              << " template" << std::endl;
    cleanup(std::ios::failbit);
    return false;
  }

  int fd(mkstemp(mTempname));

  if (fd < 0) {
    std::cerr << "Error opening " << mTempname 
              << ", " << strerror(errno) << std::endl;
    cleanup(std::ios::failbit);
    return false;
  }

  mBuf.set(fd);
  rdbuf(&mBuf);

  mFilename = filename;

  return true;
}

void
oafstream::cleanup(const std::ios::iostate bit)
{
  if (mTempname) {
    unlink(mTempname);
    free(mTempname);
    mTempname = 0;
  }
  setstate(bit);
}

inline static int
myClose(int fd) 
{
  return close(fd);
}

void
oafstream::cancel()
{
  cleanup(std::ios::failbit);
}

void
oafstream::close()
{
  if (!mTempname) {
    cleanup(std::ios::failbit);
    return;
  }
  if( !*this ) {
    cleanup(std::ios::failbit);
    return;
  }

  int fd(mBuf.fd());
  if (fd == -1) {
    std::cerr << "Error closing " << mTempname << ", never opened" << std::endl;
    cleanup(std::ios::failbit);
    return;
  }
  if (myClose(fd)) {
    std::cerr << "Error closing " << mTempname
	      << ", " << strerror(errno) << std::endl;
    cleanup(std::ios::failbit);
    return;
  }

  if (rename(mTempname, mFilename.c_str())) {
    std::cerr << "Error renaming " << mTempname << " to " << mFilename
         << ", " << strerror(errno) << std::endl;
    cleanup(std::ios::failbit);
    return;
  }

  cleanup(std::ios::goodbit);
  clear();
  return;
}
