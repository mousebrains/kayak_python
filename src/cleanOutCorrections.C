#include <InfoDB.H>

int 
main (int argc,
      char **argv)
{
  try {
    InfoDB info;
    info.cleanOutCorrections(time(0) - (24 * 3600));
    return 0;
  } catch (std::exception& e) {
    std::cerr << "Error: " << e.what() << std::endl;
    throw;
  } catch (...) {
    std::cerr << argv[0] << " unknown caught an exception" << std::endl;
  }
  return 1;

}
