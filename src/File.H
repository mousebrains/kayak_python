#ifndef INC_File_H_
#define INC_File_H_

#include <sys/stat.h>
#include <string>
#include <ctime>

class File {
private:
  bool mOkay;
  struct stat mStat;
  std::string mFilename;

  bool isModeSet(const mode_t bit) const;

public:
  File(const std::string& filename);
  File& operator= ( const std::string &filename );

  operator bool () const {return okay();}

  bool okay() const {return mOkay;}

  bool exists() const {return okay();}
  static bool exists(const std::string& filename);

  const std::string &name() const { return mFilename; }

  mode_t mode() const;
  static mode_t mode(const std::string& filename);

  off_t size() const;
  static off_t size(const std::string& filename);

  nlink_t links() const;
  static nlink_t links(const std::string& filename);

  uid_t uid() const;
  static uid_t uid(const std::string& filename);

  gid_t gid() const;
  static gid_t gid(const std::string& filename);

  time_t atime() const;
  static time_t atime(const std::string& filename);

  time_t ctime() const;
  static time_t ctime(const std::string& filename);

  time_t mtime() const;
  static time_t mtime(const std::string& filename);

  bool isMine() const;
  static bool isMine(const std::string& filename);

  bool isMyGroup() const;
  static bool isMyGroup(const std::string& filename);

  bool isExecutable() const {return isModeSet(1);}
  static bool isExecutable(const std::string& filename);

  bool isReadable() const {return isModeSet(4);}
  static bool isReadable(const std::string& filename);

  bool isWriteable() const {return isModeSet(2);}
  static bool isWriteable(const std::string& filename);

  bool isDirectory() const;
  static bool isDirectory(const std::string& filename);

  bool isSocket() const;
  static bool isSocket(const std::string& filename);

  bool isFile() const;
  static bool isFile(const std::string& filename);

  bool isCharDevice() const;
  static bool isCharDevice(const std::string& filename);

  bool isBlockDevice() const;
  static bool isBlockDevice(const std::string& filename);

  bool isFIFO() const;
  static bool isFIFO(const std::string& filename);

  bool isSymbolicLink() const;
  static bool isSymbolicLink(const std::string& filename);

  bool isSetUID() const;
  static bool isSetUID(const std::string& filename);

  bool isSetGID() const;
  static bool isSetGID(const std::string& filename);

  bool isGZipped() const;
  static bool isGZipped(const std::string& filename);

  std::string dirname() const;
  std::string extension() const;
  std::string tail() const;
  std::string rootname() const;
  std::string realPath() const;
  
  static std::string dirname(const std::string& filename);
  static std::string extension(const std::string& filename);
  static std::string tail(const std::string& filename);
  static std::string rootname(const std::string& filename);
  static std::string realPath(const std::string& filename);

  static bool makedir(const std::string& dirname,
		      const mode_t mode = 0755);

  static bool unlink(const std::string& filename,
                     const bool recursive = false);

  std::string readLink() const;
  static std::string readLink(const std::string& path);

  static bool makeLink (const std::string& oldpath,
                        const std::string& newpath);

  static bool makeSymlink (const std::string& oldpath,
                           const std::string& newpath);
};

#endif // INC_File_H_
