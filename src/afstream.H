#ifndef _afstream_H_
#define _afstream_H_

#include <fdstream.H>
#include <string>
#include <sys/stat.h>

class oafstream : public std::ostream {
protected:
  std::string mFilename; // Name to end up with
  char *mTempname;       // Name I actually opened
  fdoutbuf mBuf;         // file descriptor streambuf

  bool setup(const std::string& filename,
             const std::string& prefix);

  bool setup(const std::string& filename,
             const std::string& prefix,
             const mode_t permissions);

  void cleanup(const std::ios::iostate bit);

private: // methods which are private and undefined so they can't be used!
  oafstream();
  oafstream(const oafstream&);
  oafstream& operator =(const oafstream&);

public:
  explicit oafstream(const std::string& filename);
  oafstream(const std::string& filename,
            const mode_t permissions);
  oafstream(const std::string& filename,
            const std::string& prefix);
  oafstream(const std::string& filename,
            const std::string& prefix,
            const mode_t permissions);
  virtual ~oafstream() {close();}
  void close();
  void cancel();
};

/* POD:

=head1 NAME

oafstream - open and write a file in an atomic fashion

=head1 SYNOPSIS

class oafstream

public:

B<oafstream> (B<const string&> I<filename>, B<mode_t> I<permissions>)

B<oafstream> (B<const string&> I<filename>, B<const string&> I<prefix>, B<mode_t> I<permissions>)

B<~oafstream> ()

B<bool close> ()

B<bool cancel> ()

=head1 DESCRIPTION

Write a file to a unique name, then after successfully closing it,
rename it to the desired filename. The default permissions are owner
read/write, not readable/writeable by group or world.

=head1 EXAMPLE

{
oafstream af("filename");
af << 10 << " some sample text" << std::endl;
}

This will creat a file, "filename", with one line of text.

=head1 SEE ALSO

=head1 DIAGNOSTICS

Writes error messages to cerr.

=head1 AUTHORS

Pat Welch

=head1 DOCUMENTATION

Pat Welch

=cut
*/

#endif // _afstream_H_
