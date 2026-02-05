#include <Env.H>
#include <cstdlib>
#include <cstring>

Env::tEnv Env::sEnv;
Env::tEnv::~tEnv() {
  for( std::map<std::string,char*> :: iterator i = begin();
       i != end(); ++i ) {
    delete [] i->second;
    i->second = 0;
  }
}

bool
Env::put( const std::string &name, const std::string &value )
{
  std::string envstr = name + "=" + value;
  unsigned envlen = envstr.length()+1;
  char *envptr  = new char[envlen];
  if( !envptr )
    return false;
  strncpy( envptr, envstr.c_str(), envlen);

  tEnv::iterator i = sEnv.find(name);
  if( i != sEnv.end() ) {
    if( putenv( envptr ) ) {
      delete [] envptr;
      return false;
    }
    delete[] i->second;
    i->second = envptr;
  } else {
    sEnv.insert(tEnv::value_type( name, envptr));
    if( putenv( envptr) ) {
      delete [] envptr;
      return false;
    }
  }
  return true;
}
  
char *
Env::get( const std::string &name )
{
  return getenv( name.c_str());
}

char *
Env::get( const char *name )
{
  return getenv( name );
}

bool
Env::unset( const std::string &name )
{
#ifdef __linux__
  ::unsetenv(name.c_str()); 
  tEnv::iterator i = sEnv.find(name);
  if( i != sEnv.end() ) {
    delete[] i->second;
    sEnv.erase(i);
  }
  return true;
#else
  return put(name,"");
#endif
}
