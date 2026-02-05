#include <CMD.H>
#include <HTTP.H>

bool 
CMD::badHash(const std::string& hash, 
	     const std::string& id)
{
  if (!hash.empty())
    return false;
  HTTP::errorPage(std::cout, 404, "No hash key supplied", "No hash key supplied" + id);
  return true;
}
