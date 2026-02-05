#ifndef INC_Env_H_
#define INC_Env_H_

#include <map>
#include <string>

class Env  {
  class tEnv : private std::map<std::string,char*> {
  public:
    ~tEnv();
    using std::map<std::string,char*>::find;
    using std::map<std::string,char*>::insert;
    using std::map<std::string,char*>::begin;
    using std::map<std::string,char*>::erase;
    using std::map<std::string,char*>::end;
    using std::map<std::string,char*>::iterator;
    using std::map<std::string,char*>::value_type;
    using std::map<std::string,char*>::const_iterator;
  };
  static tEnv sEnv;
  Env(const Env &e);
public:
  Env() {};
  static bool put( const std::string &name, const std::string &value);
  static char *get( const std::string &name );
  static char *get( const char *name );
  static bool unset( const std::string &name );
};

#endif // INC_Env_H_
