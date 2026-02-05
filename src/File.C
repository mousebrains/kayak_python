#include <File.H>
#include <Directory.H>
#include <RegExp.H>
#include <errno.h>
#include <sys/types.h>
#include <unistd.h>
#include <cstdio>
#include <iostream>
#include <fstream>

File::File(const std::string& filename)
  :  mFilename(filename)
{
  mOkay = !stat(filename.c_str(), &mStat);
}

File &
File::operator= (const std::string& filename)
{
  mOkay = !stat(filename.c_str(), &mStat);
  mFilename = filename;
  return *this;
}

bool
File::exists(const std::string& filename)
{
  return File(filename).exists();
}

mode_t
File::mode() const
{
  return mOkay ? mStat.st_mode : 0;
}

mode_t
File::mode(const std::string& filename)
{
  return File(filename).mode();
}

off_t
File::size() const
{
  return mOkay ? mStat.st_size : 0;
}

off_t
File::size(const std::string& filename)
{
  return File(filename).size();
}

nlink_t
File::links() const
{
  return mOkay ? mStat.st_nlink : 0;
}

nlink_t
File::links(const std::string& filename)
{
  return File(filename).links();
}

uid_t
File::uid() const
{
  return mOkay ? mStat.st_uid : 0;
}

uid_t
File::uid(const std::string& filename)
{
  return File(filename).uid();
}

gid_t
File::gid() const
{
  return mOkay ? mStat.st_gid : 0;
}

gid_t
File::gid(const std::string& filename)
{
  return File(filename).gid();
}

time_t
File::atime() const
{
  return mOkay ? mStat.st_atime : 0;
}

time_t
File::atime(const std::string& filename)
{
  return File(filename).atime();
}

time_t
File::ctime() const
{
  return mOkay ? mStat.st_ctime : 0;
}

time_t
File::ctime(const std::string& filename)
{
  return File(filename).ctime();
}

time_t
File::mtime() const
{
  return mOkay ? mStat.st_mtime : 0;
}

time_t
File::mtime(const std::string& filename)
{
  return File(filename).mtime();
}

bool 
File::isMine() const
{
  return mOkay ? (geteuid() == uid()) : false;
}

bool 
File::isMine(const std::string& filename) 
{
  return File(filename).isMine();
}

bool 
File::isMyGroup() const
{
  return mOkay ? (getegid() == gid()) : false;
}

bool 
File::isMyGroup(const std::string& filename) 
{
  return File(filename).isMyGroup();
}

bool
File::isModeSet(const mode_t bit) const
{
  if (!mOkay)
    return false;

  const mode_t m(mode());
  const mode_t gbit(bit << 3);
  const mode_t obit(bit << 6);

  if (m & bit) // World accessable
    return true;

  if (!(m & (gbit | obit))) // Check if group or owner possible
    return false;

  return ((isMyGroup() && (m & (bit << 3))) || 
          (isMine() && (m & (bit << 6))));
}

bool
File::isExecutable(const std::string& filename)
{
  return File(filename).isExecutable();
}

bool
File::isReadable(const std::string& filename)
{
  return File(filename).isReadable();
}

bool
File::isWriteable(const std::string& filename)
{
  return File(filename).isWriteable();
}

bool
File::isDirectory() const
{
  return mOkay ? S_ISDIR(mode()) : false;
}

bool
File::isDirectory(const std::string& filename)
{
  return File(filename).isDirectory();
}

bool
File::isSocket() const
{
  return mOkay ? S_ISSOCK(mode()) : false;
}

bool
File::isSocket(const std::string& filename)
{
  return File(filename).isSocket();
}

bool
File::isFile() const
{
  return mOkay ? S_ISREG(mode()) : false;
}

bool
File::isFile(const std::string& filename)
{
  return File(filename).isFile();
}

bool
File::isCharDevice() const
{
  return mOkay ? S_ISCHR(mode()) : false;
}

bool
File::isCharDevice(const std::string& filename)
{
  return File(filename).isCharDevice();
}

bool
File::isBlockDevice() const
{
  return mOkay ? S_ISBLK(mode()) : false;
}

bool
File::isBlockDevice(const std::string& filename)
{
  return File(filename).isBlockDevice();
}

bool
File::isFIFO() const
{
  return mOkay ? S_ISFIFO(mode()) : false;
}

bool
File::isFIFO(const std::string& filename)
{
  return File(filename).isFIFO();
}

bool
File::isSymbolicLink() const
{
  return mOkay ? S_ISLNK(mode()) : false;
}

bool
File::isSymbolicLink(const std::string& filename)
{
  return File(filename).isSymbolicLink();
}

bool
File::isSetUID() const
{
  return mOkay ? (mode() & S_ISUID) : false;
}

bool
File::isSetUID(const std::string& filename)
{
  return File(filename).isSetUID();
}

bool
File::isSetGID() const
{
  return mOkay ? (mode() & S_ISGID) : false;
}

bool
File::isSetGID(const std::string& filename)
{
  return File(filename).isSetGID();
}

bool
File::isGZipped() const
{
  return isGZipped(mFilename);
}

// Simple test, should use magic file

bool
File::isGZipped(const std::string& filename)
{
  std::ifstream is(filename.c_str());

  if (!is)
    return false;

  const unsigned char c0(is.get());
  if (c0 != 0x1f)
    return false;

  return is.get() == 0x8b;
}

std::string 
File::dirname() const
{
  const std::string::size_type i(mFilename.rfind('/'));

  if (i == mFilename.npos)
    return "./";

    // Deal with a trailing /

  if (i != (mFilename.size() - 1)) 
    return mFilename.substr(0, i + 1);

  if (mFilename.size() == 1)
    return mFilename;

  return dirname(mFilename.substr(0, mFilename.size() - 1));
}

std::string
File::dirname( const std::string &filename)
{
  return File(filename).dirname();
}

std::string 
File::extension() const
{
  const std::string fn(tail(mFilename));
  const std::string::size_type j(fn.rfind('.'));

  return (j == mFilename.npos) ? std::string() : fn.substr(j);
}


std::string 
File::extension(const std::string& filename)
{
  return File(filename).extension();
}

std::string 
File::tail() const
{
  const std::string::size_type i(mFilename.rfind('/'));

  if (i == mFilename.npos)
    return mFilename;

    // Deal with a trailing /

  if (i != (mFilename.size() - 1)) 
    return mFilename.substr(i + 1);

  if (mFilename.size() == 1)
    return std::string();

  return tail(mFilename.substr(0, mFilename.size() - 1));
}

std::string 
File::tail(const std::string& filename)
{
  return File(filename).tail();
}

std::string 
File::rootname() const
{
  const std::string::size_type j(mFilename.rfind('.'));

  if (j == mFilename.npos)
    return mFilename;

  const std::string::size_type i(mFilename.rfind('/'));

  if ((i != mFilename.npos) && (i > j))
    return mFilename;

  return mFilename.substr(0, j);
}

std::string 
File::rootname(const std::string& filename)
{
  return File(filename).rootname();
}

std::string
File::realPath() const
{
  char buffer[PATH_MAX+1];

  if (realpath(mFilename.c_str(), buffer))
    return std::string(buffer);

  return std::string();
}

std::string
File::realPath(const std::string& filename)
{
  return File(filename).realPath();
}

bool
File::makedir(const std::string& directory,
              const mode_t mode)
{
  if (directory.empty()) 
    return false;

  const char *dir(directory.c_str());

  if (!mkdir(dir, mode))
    return true;

  if (errno == EEXIST) 
    return isDirectory(dir);

  if (errno != ENOENT) 
    return false;

  if (!makedir(dirname(directory), mode)) 
    return false;

  return !mkdir(dir, mode);
}

bool
File::unlink (const std::string& filename,
		 const bool recursive)
{
  const char *fn(filename.c_str());

  if (recursive ) {
    std::list<File> dir;
    if( !Directory(fn).fill(back_inserter(dir)))
      return false;
    
    for (std::list<File>::const_iterator it = dir.begin();
	 it != dir.end(); ++it) {
      const std::string& name(it->name());
      if ((name != ".") && (name != "..")) 
	File::unlink(name, it->isDirectory());
    }
    return !rmdir(fn);
  } 
  return !remove(fn);
}

std::string
File::readLink (const std::string& path)
{
  char buffer[2048];
  const int len(readlink(path.c_str(), buffer, sizeof(buffer) - 1));

  if (len < 0)
    return std::string();

  return std::string(buffer, len);
}

bool
File::makeLink (const std::string& oldpath,
                const std::string& newpath)
{
  return !link(oldpath.c_str(), newpath.c_str());
}

bool
File::makeSymlink (const std::string& oldpath,
                   const std::string& newpath)
{
  return !symlink(oldpath.c_str(), newpath.c_str());
}
