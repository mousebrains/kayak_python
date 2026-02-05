#include <pstream.H>
#include <cstdlib>
#include <sys/types.h>
#include <sys/wait.h>

opstream::opstream( const std::string& command,
		      const std::vector<std::string> &args)
  : ofdstream(-1), mPid(-1)
{
  int fildes[2];
  if( !pipe( fildes ) ) {
    pid_t pid = fork();
    if( pid == -1 ) {
	close( fildes[0] );
	close( fildes[1] );
	setstate(std::ios::failbit);
	; // error - leave fd at -1 to tell the caller
    } else if( pid ) {
	// parent - set the writing end of the pipe
	mBuf.set(fildes[1]);
	mPid     = pid;
	close(fildes[0]);
    } else {
	dup2(fildes[0], 0);
	close( fildes[1] );
	char **argv = reinterpret_cast<char **>
	  (calloc( args.size() + 2, sizeof(char*) ));
	argv[0] = strdup(command.c_str());
	for( unsigned i = 0; i < args.size(); ++i )
	  argv[1+i] = strdup(args[i].c_str());
	execv( argv[0], argv );
	exit(1);			// just in case
    }
  } else {
    setstate(std::ios::failbit);
  }
}

opstream::~opstream()
{
  if( mBuf.fd() >= 0 )
    close( mBuf.fd() );		// we own it here - so we close it
  if( mPid > 0 ) {
    int status;
    waitpid( mPid, &status, 0);
  }
}


ipstream::ipstream( const std::string& command,
		      const std::vector<std::string> &args)
  : ifdstream(-1), mPid(-1)
{
  int fildes[2];
  if( !pipe( fildes ) ) {
    pid_t pid = fork();
    if( pid == -1 ) {
	close( fildes[0] );
	close( fildes[1] );
	setstate(std::ios::failbit);
	; // error - leave fd at -1 to tell the caller
    } else if( pid ) {
	// parent - set the reading end of the pipe
	buf.set(fildes[0]);
	mPid     = pid;
	close(fildes[1]);
    } else {
	dup2(fildes[1], 1);
	close( fildes[0] );
	char **argv = reinterpret_cast<char **>
	  (calloc( args.size() + 2, sizeof(char*) ));
	argv[0] = strdup(command.c_str());
	for( unsigned i = 0; i < args.size(); ++i )
	  argv[1+i] = strdup(args[i].c_str());
	execv( argv[0], argv );
	exit(1);			// just in case
    }
  } else {
    setstate(std::ios::failbit);
  }
}



ipstream::~ipstream()
{
  if( buf.fd() >= 0 )
    close( buf.fd() );		// we own it here - so we close it
  if( mPid > 0 ) {
    int status;
    waitpid( mPid, &status, 0);
  }
}
