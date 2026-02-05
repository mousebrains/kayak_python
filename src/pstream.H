#ifndef INC_pstream_H_
#define INC_pstream_H_

#include <fdstream.H>
#include <cstdio>
#include <string>
#include <vector>
#include <unistd.h>

class opstream : public ofdstream {
private: // methods which are private and undefined so they can't be used!
  opstream();
  opstream(const opstream&);
  opstream& operator =(const opstream&);
  pid_t mPid;
public:
  opstream(const std::string& command, const std::vector<std::string> &args);
  virtual ~opstream();
};

class ipstream : public ifdstream {
private:
  pid_t mPid;
public:
  ipstream (const std::string& command, const std::vector<std::string> &args);
  virtual ~ipstream();
};

/* POD:

=head1 NAME

opstream - open and write/read to/from a pipe using popen

=head1 SYNOPSIS

class opstream

public:

B<opstream> (B<const std::string> I<command>)

B<~opstream> ()

=head1 DESCRIPTION

Write to a piped command

=head1 EXAMPLE

{
opstream os("command");
os << 10 << " some sample text" << std::endl;
}

This will write to "command" one line

=head1 SEE ALSO

=head1 DIAGNOSTICS

standard ostream diagnostics

=head1 AUTHORS

Pat Welch

=head1 DOCUMENTATION

Pat Welch

=cut
*/

#endif // INC_pstream_H_
