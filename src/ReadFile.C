#include <ReadFile.H>
#include <fstream>
#include <iostream>
#include <cerrno>
#include <cstring>

std::string
ReadFile(const std::string& filename,
         const bool complain)
{
  std::ifstream is(filename.c_str());

  if (!is) {
    if (complain)
      std::cerr << "Error opening '" << filename << "', " << strerror(errno) << std::endl;
    return std::string();
  }

  std::string result;

  for (std::string line; getline(is, line);) {
    result += line;
    result += "\n";
  }
  return result;
}
