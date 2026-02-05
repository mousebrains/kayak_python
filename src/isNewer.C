#include <isNewer.H>
#include <File.H>

bool
isNewer(const std::string& filename,
        const time_t rtime)
{
  const time_t mtime(File::mtime(filename));
  return (mtime > rtime);
}
