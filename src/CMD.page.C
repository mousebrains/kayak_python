#include <CMD.H>
#include <PageArchive.H>
#include <iostream>

int CMD::page(const std::string& name) 
{
  return PageArchive::spewPage(name, std::cout);
}
