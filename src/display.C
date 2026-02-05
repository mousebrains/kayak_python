#include <CMD.H>
#include <CGI.H>
#include <HTTP.H>

int
main (int argc,
      char **argv)
{
  const CGI cgi;
  
  try {
    if (cgi.empty() || cgi.isSet("M")) return CMD::page("main");
    if (cgi.isSet("P")) return CMD::page(cgi.get("P"));
    if (cgi.isSet("F")) return CMD::file(cgi.get("F"));
    if (cgi.isSet("f")) return CMD::plot(cgi, "f", cgi.get("f"), "flow", "Flow(CFS)");
    if (cgi.isSet("g")) return CMD::plot(cgi, "g", cgi.get("g"), "gage", "Gauge(Ft)");
    if (cgi.isSet("t")) return CMD::plot(cgi, "t", cgi.get("t"), "temperature", "Temperatur(F)");
    if (cgi.isSet("v")) return CMD::view(cgi, cgi.get("v"));
    if (cgi.isSet("e")) return CMD::edit(cgi.get("e"));
    if (cgi.isSet("d")) return CMD::page("d");
    if (cgi.isSet("D")) return CMD::description(cgi.get("D"));
    if (cgi.isSet("q")) return CMD::page("main"); // Handle redirect which sticks in q=

    HTTP::errorPage(std::cout, 404, "Unrecognized command", "Unrecognized command");
    return 1;
  } catch (std::exception& e) {
    std::cerr << argv[0] << " caught an exception, " << e.what() << std::endl;
    HTTP::errorPage(std::cout, 404, "Exception", std::string("Exception caught, ") + e.what());
  } catch (...) {
    std::cerr << argv[0] << " unknown caught an exception" << std::endl;
    HTTP::errorPage(std::cout, 404, "Exception", "Unknown Exception caught");
  }
  return 1;
}
