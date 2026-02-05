#include <InfoDB.H>
#include <File.H>
#include <URLparse.H>
#include <Parse_USGS.H>
#include <Parse_CBRFC.H>
#include <Parse_NOAA.H>
#include <Parse_NOAA2.H>
#include <Parse_NWRFC.H>
#include <Parse_NWRFC_XML.H>
#include <Parse_OCS.H>
#include <Parse_Waves.H>
#include <Parse_USBR.H>
#include <Parse_USBR_Special.H>
#include <Parse_USBR_PN.H>
#include <Parse_USACE_Ca.H>
#include <Parse_IDWR.H>
#include <Parse_Wa_Gov.H>
#include <Parse_USACE_Resv.H>
#include <Parse_USACE_Outflow.H>
#include <Parse_NOAA_XML.H>
#include <Parse_IdahoPower.H>
#include <Curl.H>
#include <iostream>
#include <unistd.h>

namespace {
  const char *options("dfhino:P:p:t:u:U:v");

  void usage(const char *argv0) {
    std::cerr << argv0 << " -[" << options << "]" << std::endl;
    std::cerr << std::endl;
    std::cerr << "-d          dry run, do not actually store any data" << std::endl;
    std::cerr << "-f          fetch pages, do not parse" << std::endl;
    std::cerr << "-h          display this message" << std::endl;
    std::cerr << "-i          ignore all constraints" << std::endl;
    std::cerr << "-n          show name being fetched" << std::endl;
    std::cerr << "-o dir      output directory for fetched information" << std::endl;
    std::cerr << "-p parser   type of parser to select MySQL records on" << std::endl;
    std::cerr << "-P dir      prepend this directory to all URLs being requestd" << std::endl;
    std::cerr << "            something of the form file://`pwd`/urls/" << std::endl;
    std::cerr << "-t type     type of parser to use(usgs, cbrfc, noaa, null)" << std::endl;
    std::cerr << "-u URL      URL to select MySQL records on" << std::endl;
    std::cerr << "-U URL      URL to operator on" << std::endl;
    std::cerr << "-v          Verbose" << std::endl;
  }

  void
  dumpFile(Curl& curl, 
           const std::string filename)
  {
    const std::string dir(File::dirname(filename));
    if (File::makedir(dir)) {
      curl.writeFile(filename);
    } else
      std::cerr << "Error making directory(" << dir << ")" << std::endl;
  }

  Parsers::Parse *
  makeParser(const std::string& type,
	     const Curl& curl,
             const bool qVerbose,
             const bool qDryRun,
	     DataDB& db)
  {
    if (type == "usgs")
      return new Parsers::USGS(curl, qVerbose, qDryRun, db);
    if (type == "cbrfc")
      return new Parsers::CBRFC(curl, qVerbose, qDryRun, db);
    if (type == "noaa")
      return new Parsers::NOAA(curl, qVerbose, qDryRun, db);
    if (type == "noaa2")
      return new Parsers::NOAA2(curl, qVerbose, qDryRun, db);
    if (type == "null")
      return 0;
    if (type == "nwrfc")
      return new Parsers::NWRFC(curl, qVerbose, qDryRun, db);
    if (type == "nwrfc.xml")
      return new Parsers::NWRFC_XML(curl, qVerbose, qDryRun, db);
    if (type == "ocs")
      return new Parsers::OCS(curl, qVerbose, qDryRun, db);
    if (type == "ocean.newport")
      return new Parsers::Waves(curl, qVerbose, qDryRun, db);
    if (type == "usbr")
      return new Parsers::USBR(curl, qVerbose, qDryRun, db);
    if (type == "usbr.special")
      return new Parsers::USBR_Special(curl, qVerbose, qDryRun, db);
    if (type == "usbr.pn")
      return new Parsers::USBR_PN(curl, qVerbose, qDryRun, db);
    if (type == "idwr")
      return new Parsers::IDWR(curl, qVerbose, qDryRun, db);
    if (type == "wa.gov")
      return new Parsers::Wa_Gov(curl, qVerbose, qDryRun, db);
    if (type == "usace.resv")
      return new Parsers::USACE_Resv(curl, qVerbose, qDryRun, db);
    if (type == "usace.outflow")
      return new Parsers::USACE_Outflow(curl, qVerbose, qDryRun, db);
    if (type == "usace.ca")
      return new Parsers::USACE_Ca(curl, qVerbose, qDryRun, db);
    if (type == "noaa.xml")
      return new Parsers::NOAA_XML(curl, qVerbose, qDryRun, db);
    if (type == "idahoPower")
      return new Parsers::IdahoPower(curl, qVerbose, qDryRun, db);

    std::cerr << "Unregonized parser type '" << type << "'" << std::endl;
    exit(1);
  }
}

int
main (int argc,
      char **argv)
{
  try {
    std::string parser;
    std::string dumpPrefix;
    bool ignoreConstraints(false);
    std::string urlPrefix;
    std::string parserCriteria;
    std::string urlCriteria;
    std::string url;
    bool verbose(false);
    bool dryRun(false);
    bool fetchOnly(false);
    bool qNameDisplay(false);

    for (int c; (c = getopt(argc, argv, options)) != EOF;) {
      switch(c) {
      case 'd': dryRun = true; break;
      case 'f': fetchOnly = true; break;
      case 'i': ignoreConstraints = true; break;
      case 'n': qNameDisplay = true; break;
      case 'o': dumpPrefix = optarg; break;
      case 'P': urlPrefix = optarg; break;
      case 'p': parserCriteria = optarg; break;
      case 't': parser = optarg; break;
      case 'u': urlCriteria = optarg; break;
      case 'U': url = optarg; break;
      case 'v': verbose = true; break;
      default: 
        std::cerr << "Unrecognized option, '" << ((char) c) << std::endl;
      case 'h': usage(argv[0]); exit(1);
      }
    }
 
    if (parser.empty() || url.empty()) { // use MySQL records
      InfoDB info;
      DataDB dataDB;
      URLparse records(info, urlCriteria, parserCriteria);

      for (URLparse::const_iterator it = records.begin(); it != records.end(); ++it) {
        if (!ignoreConstraints && !it->qHour()) {
          if (verbose)
            std::cerr << "Skipping '" << it->url() << "' due to hour constraint" << std::endl;
          continue;
	}
 
        const std::string& url(it->url());
        const std::string& parser(it->parser());
        const std::string& hours(it->hours());
        if (verbose || qNameDisplay)
          std::cerr << "Working on " << urlPrefix << url << " parser " << parser 
                    << " hours " << hours << std::endl;
        try {
          Curl curl(urlPrefix + url.c_str());

	  if (curl) { // Did something okay
            if (curl.responseCode() >= 400) {
              std::cerr << "response code " << curl.responseCode() << " for " << url << std::endl;
            } else {
              if (!dumpPrefix.empty())
  	        dumpFile(curl, dumpPrefix + url);
  
              if (!fetchOnly) {
                const Parsers::Parse *ptr(makeParser(parser, curl, verbose, dryRun, dataDB));
                delete ptr;
              }
            }
          }
        } catch (std::exception& e) {
          std::cerr << "Caught exception for url(" << url << ")" << std::endl;
          std::cerr << e.what() << std::endl;
        } catch (...) {
          std::cerr << "Unknown exception thrown for url(" << url << ")" << std::endl;
        }
      }
    } else {
      try {
        Curl curl(urlPrefix + url.c_str());
        if (curl) {
          if (curl.responseCode() >= 400) {
            std::cerr << "response code " << curl.responseCode() << " for " << url << std::endl;
          } else {
            if (!dumpPrefix.empty())
  	      dumpFile(curl, dumpPrefix + url);
            DataDB dataDB;
            const Parsers::Parse *ptr(makeParser(parser, curl, verbose, dryRun, dataDB));
            delete ptr;
          }
        }
      } catch (std::exception& e) {
        std::cerr << "Caught exception for url(" << url << ")" << std::endl;
        std::cerr << e.what() << std::endl;
      } catch (...) {
        std::cerr << "Unknown exception thrown for url(" << url << ")" << std::endl;
      }
    }

    Curl::wrapup(); // Clean up curl

    return 0;

  } catch (std::exception& e) {
    std::cerr << "Exception caught, " << e.what() << std::endl;
  }
  return 1;
}
