#ifndef _fdstream_H_
#define _fdstream_H_

// Most of this was lifted from Josuttis, with some code cleanup
// http://www.josuttis.com/cppcode/fdstream.hpp.html

#include <iostream>
#include <unistd.h>
#include <cstring>
#include <cstdio>

class fdoutbuf : public std::streambuf {
protected:
  int mFD;
public:
  explicit fdoutbuf(int fd) : mFD(fd) {}
  fdoutbuf() : mFD(-1) {}
  virtual ~fdoutbuf() {}
  int fd() const {return mFD;}
  void set(int fd) { mFD = fd; }
protected:
  virtual int overflow(int c) {
    if (c == EOF)
      return EOF;
    const char z(c);
    return (write(mFD, &z, 1) == 1) ? c : EOF;
  }
  virtual std::streamsize xsputn(const char *s, std::streamsize num) {
    return write(mFD, s, num);
  }
};


class ofdstream : public std::ostream {
protected:
  fdoutbuf mBuf;
private: // methods which are private and undefined so they can't be used!
  ofdstream();
  ofdstream(const ofdstream&);
  ofdstream& operator =(const ofdstream&);
public:
  ofdstream(int fd) : std::ostream(0), mBuf(fd) { rdbuf(&mBuf); }
  virtual ~ofdstream() {}
};

/************************************************************
 * ifdstream
 * - a stream that writes on a file descriptor
 ************************************************************/

class fdinbuf : public std::streambuf {
protected:
  int mFD;    // file descriptor
protected:
  /* data buffer:
   * - at most, pbSize characters in putback area plus
   * - at most, bufSize characters in ordinary read buffer
   */
  static const int pbSize = 4;        // size of putback area
  static const int bufSize = 1024;    // size of the data buffer
  char buffer[bufSize+pbSize];        // data buffer

public:
  /* constructor
   * - initialize file descriptor
   * - initialize empty data buffer
   * - no putback area
   * => force underflow()
   */
  fdinbuf (int _fd) : mFD(_fd) {
    setg (buffer+pbSize,     // beginning of putback area
	  buffer+pbSize,     // read position
	  buffer+pbSize);    // end position
  }
  int fd() const {return mFD;}
  void set(int fd) { mFD = fd; }

protected:
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
    num = read (mFD, buffer+pbSize, bufSize - pbSize);
    if (num <= 0) {
      // ERROR or EOF
      return EOF;
    }

    // reset buffer pointers
    setg (buffer+(pbSize-numPutback),   // beginning of putback area
	  buffer+pbSize,                // read position
	  buffer+pbSize+num);           // end of buffer

    // return next character
    return *gptr();
  }
};

class ifdstream : public std::istream {
protected:
  fdinbuf buf;
public:
  ifdstream (int fd) : std::istream(0), buf(fd) { rdbuf(&buf); }
  virtual ~ifdstream() {}
};

/* POD:

=head1 NAME

ofdstream - open and write a to a file descriptor

=head1 SYNOPSIS

class ofdstream

public:

B<ofdstream> (B<int> I<fileDescriptor>)

B<~ofdstream> ()

=head1 DESCRIPTION

Write to a file descriptor.

=head1 EXAMPLE

{
int fd(open("filename", O_CREAT | O_TRUNC));
ofdstream os(fd);
os << 10 << " some sample text" << std::endl;
}

This will create a file, "filename", with one line of text.

=head1 SEE ALSO

=head1 DIAGNOSTICS

standard ostream diagnostics

=head1 AUTHORS

Pat Welch

=head1 DOCUMENTATION

Pat Welch

=cut
*/

#endif // _fdstream_H_
