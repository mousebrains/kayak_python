#include <InfoDB.H>

int
main (int argc,
      char **argv)
{
  try {
    InfoDB info;
    info.mkMergedMaster();
    return 0;
  } catch (std::exception& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    throw;
  }
  return 1;
}
