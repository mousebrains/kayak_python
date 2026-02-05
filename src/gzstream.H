#ifndef _gzstream_H_
#define _gzstream_H_

// Most of this was lifted from Josuttis, with some code cleanup
// http://www.josuttis.com/cppcode/fdstream.hpp.html

#include <zlib.h>
#include <iostream>

#if defined(__GNUC__) && (__GNUC__ == 2)
#include <streambuf.h>
#else
#include <streambuf>
#endif

#define GZSTREAM_REPORTER std::cerr

#include <string>
#include <unistd.h>
#include <cstring>
#include <errno.h>

class gzoutbuf : public std::streambuf {
protected:
  gzFile mHandle;
public:
#if defined(__GNUC__) && (__GNUC__ == 2)
  typedef streampos pos_type;
  typedef streamoff off_type; 
#endif

  explicit gzoutbuf(gzFile handle) : mHandle(handle) {}
  gzoutbuf() : mHandle(0) {}
  virtual ~gzoutbuf() {}
  gzFile handle() const {return mHandle;}
  void set(gzFile handle) { mHandle = handle; }
protected:
  virtual pos_type seekoff(off_type offset, 
#if (defined(__GNUC__) && (__GNUC__ >= 3)) || defined(__INTEL_COMPILER)
			   std::ios_base::seekdir dir, 
			   std::ios_base::openmode /* mode */) {
#else
			   std::ios::seekdir dir, 
			   int /* mode */) {
#endif // (__GNUC__ >= 3)
    if (dir == std::ios::end) 
      return -1;

    pos_type pos(gztell(mHandle));
    if ((offset == 0) && (dir == std::ios::cur))
      return pos;

    if (gzseek(mHandle, offset, 
               (dir == std::ios::beg) ? SEEK_SET : SEEK_CUR) < 0) 
      return -1;

    return pos;
  }

#if (defined(__GNUC__) && (__GNUC__ >= 3)) || defined(__INTEL_COMPILER)
  virtual pos_type seekpos(pos_type position, 
                           std::ios_base::openmode mode) {
    return seekoff(position, std::ios_base::beg, mode);
  }
#else
  virtual pos_type seekpos(pos_type position, 
                           std::ios::openmode mode) {
    return seekoff(position, std::ios::beg, mode);
  }
#endif // (__GNUC__ >= 3)

  virtual int overflow(int c) {
    if (c == EOF)
      return EOF;
    const char z(c);
    return (gzwrite(mHandle, const_cast<char *>(&z), 1) == 1) ? c : EOF;
  }
  virtual std::streamsize xsputn(const char *s, std::streamsize num) {
    return gzwrite(mHandle, (void *)(s), num);
  }
};


class ogzstream : public std::ostream {
protected:
  std::string mFilename;
  bool mIsOpen;
  gzoutbuf mBuf;
private: // methods which are private and undefined so they can't be used!
  ogzstream();
  ogzstream(const ogzstream&);
  ogzstream& operator =(const ogzstream&);
public:
  explicit ogzstream(const std::string& filename,
		     const std::string& mode = std::string())
    : std::ostream(0), 
      mFilename(filename),
      mIsOpen(false)
  {
    open(filename, mode);
  }
  ogzstream(int fd,
            const std::string& filename,
            const std::string& mode = std::string())
    : std::ostream(0), 
      mFilename(filename),
      mIsOpen(false)
  {
    open(fd, filename, mode);
  }
  virtual ~ogzstream() {close();}

  bool is_open() const {return mIsOpen;}

  void open(const std::string& filename,
	    const std::string& mode = std::string())
  {
    open(-1, filename, mode);
  }

  void open(int fd,
	    const std::string& filename,
	    const std::string& mode = std::string())
  {
    if (is_open())
      close();

    mFilename = filename;

    std::string omode = mode;
    if( omode.find_first_of("aw") == omode.npos )
      omode = "w" + omode;
    if( omode.find_first_of("b") == omode.npos )
      omode = omode + "b";

    gzFile handle(
		  fd < 0?
		  gzopen(filename.c_str(), omode.c_str())
		  :
		  gzdopen(fd, omode.c_str())
		  );
    if (!handle) {
      setstate(std::ios::failbit);
      GZSTREAM_REPORTER << "Error opening " << filename << ", "
			<< strerror(errno) << std::endl;
      return;
    }
    mBuf.set(handle);
    rdbuf(&mBuf);
    mIsOpen = true;
  }

  void close() {
    if (!is_open())
      return;

    mIsOpen = false;

    gzFile handle(mBuf.handle());
    if (!handle) {
      setstate(std::ios::failbit);
      return;
    }
    if (gzclose(handle)) {
      setstate(std::ios::failbit);
      GZSTREAM_REPORTER << "Error closing " << mFilename << ", "
			<< strerror(errno) << std::endl;
      return;
    }
    setstate(std::ios::goodbit);
  }
};

/************************************************************
 * igzstream
 * - a stream that writes on a file descriptor
 ************************************************************/

class gzinbuf : public std::streambuf {
protected:
  gzFile mHandle;    // file handle
protected:
  /* data buffer:
   * - at most, pbSize characters in putback area plus
   * - at most, bufSize characters in ordinary read buffer
   */
  static const int pbSize = 4;        // size of putback area
  static const int bufSize = 32000;   // size of the data buffer
  char buffer[bufSize+pbSize];        // data buffer

public:
#if defined(__GNUC__) && (__GNUC__ == 2)
  typedef streampos pos_type;
  typedef streamoff off_type;
#endif

  /* constructor
   * - initialize file descriptor
   * - initialize empty data buffer
   * - no putback area
   * => force underflow()
   */
  gzinbuf (gzFile handle) : mHandle(handle) {
    setg (buffer+pbSize,     // beginning of putback area
	  buffer+pbSize,     // read position
	  buffer+pbSize);    // end position
  }

  gzFile handle() const {return mHandle;}

  void set(gzFile handle) {mHandle = handle;}

protected:
  // Seek to a specified position

  virtual pos_type seekoff(off_type offset, 
#if (defined(__GNUC__) && (__GNUC__ >= 3)) || defined(__INTEL_COMPILER)
			   std::ios_base::seekdir dir, 
			   std::ios_base::openmode /* mode */) {
#else
			   std::ios::seekdir dir, 
			   int /* mode */) {
#endif // (__GNUC__ >= 3)
    if (dir == std::ios::end) 
      return -1;

    pos_type pos(gztell(mHandle));
    if ((offset == 0) && (dir == std::ios::cur))
      return pos;

    if (gzseek(mHandle, offset, 
               (dir == std::ios::beg) ? SEEK_SET : SEEK_CUR) < 0) 
      return -1;

    setg (buffer+pbSize,     // beginning of putback area
	  buffer+pbSize,     // read position
	  buffer+pbSize);    // end position

    return pos;
  }

#if (defined(__GNUC__) && (__GNUC__ >= 3)) || defined(__INTEL_COMPILER)
  virtual pos_type seekpos(pos_type position, 
                           std::ios_base::openmode mode) {
    return seekoff(position, std::ios_base::beg, mode);
  }
#else
  virtual pos_type seekpos(pos_type position, 
                           int mode) {
    return seekoff(position, std::ios::beg, mode);
  }
#endif // (__GNUC__ >= 3)

  // insert new characters into the buffer
  virtual int underflow () {
#ifndef _MSC_VER
    using std::memcpy;
#endif

    // is read position before end of buffer?
    if (gptr() < egptr()) {
      return *gptr();
    }

    /* process size of putback area
     * - use number of characters read
     * - but at most size of putback area
     */
    int numPutback;
    numPutback = gptr() - eback();
    if (numPutback > pbSize) {
      numPutback = pbSize;
    }

    /* copy up to pbSize characters previously read into
     * the putback area
     */
    memcpy (buffer+(pbSize-numPutback), gptr()-numPutback,
	    numPutback);

    // read at most bufSize new characters
    int num;
    num = gzread (mHandle, buffer+pbSize, bufSize);
    if (num <= 0) {
      // ERROR or EOF
      return EOF;
    }

    // reset buffer pointers
    setg (buffer+(pbSize-numPutback),   // beginning of putback area
	  buffer+pbSize,                // read position
	  buffer+pbSize+num);           // end of buffer

    // no need to return character at read position - uflow does that
    // we'll just indicate that we're not at EOF
    return (!EOF);
  }
};

class igzstream : public std::istream {
protected:
  std::string mFilename;
  bool mIsOpen;
  gzinbuf mBuf;
public:
  explicit igzstream (const std::string& filename) 
    : std::istream(0), 
      mFilename(filename),
      mIsOpen(false),
      mBuf(0)
  {
    open(filename);
  }
  igzstream (int fd,
	     const std::string& filename) 
    : std::istream(0), 
      mFilename(filename),
      mIsOpen(false),
      mBuf(0)
  {
    open(fd, filename);
  }
  ~igzstream() { close(); }

  bool is_open() const {return mIsOpen;}

  void open(const std::string& filename,
	    const std::string& mode = std::string())
  {
    open(-1, filename, mode);
  }

  void open(int fd,
	    const std::string& filename,
	    const std::string& /* mode */ = std::string())
  {
    if (is_open())
      close();

    mFilename = filename;

    gzFile handle(
		  fd < 0?
		  gzopen(filename.c_str(), "rb")
		  :
		  gzdopen(fd, "rb")
		  );
    if (!handle) {
      setstate(std::ios::failbit);
      GZSTREAM_REPORTER << "Error opening " << filename << ", "
			<< strerror(errno) << std::endl;
      return;
    }
    mBuf.set(handle);
    rdbuf(&mBuf);
    mIsOpen = true;
  }

  void close() {
    if (!is_open())
      return;

    mIsOpen = false;

    gzFile handle(mBuf.handle());
    if (!handle) {
      setstate(std::ios::failbit);
      return;
    }
    if (gzclose(handle)) {
      setstate(std::ios::failbit);
      GZSTREAM_REPORTER << "Error closing " << mFilename << ", "
			<< strerror(errno) << std::endl;
      return;
    }
    setstate(std::ios::goodbit);
  }
};

#undef GZSTREAM_REPORTER

/* POD:

=head1 NAME

ogzstream - open and write a to a compressed file

=head1 SYNOPSIS

class ogzstream

public:

=head1 DIAGNOSTICS

standard ostream diagnostics

=head1 AUTHORS

Pat Welch

=head1 DOCUMENTATION

Pat Welch

=cut
*/

#endif // _gzstream_H_
