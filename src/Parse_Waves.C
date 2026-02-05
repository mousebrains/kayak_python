#include <Parse_Waves.H>
#include <File.H>
#include <Tokenize.H>
#include <DataDB.H>
#include <String.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  Waves::Waves(const Curl& curl,
               const bool qVerbose,
               const bool qDryRun,
	       DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db),
      mStation(String::toUpper(File::tail(mURL)))
  {
    serveUpLines(curl.str()); 
  }

  bool
  Waves::line(const std::string& l)
  {
    Tokenize tokens(l);

    if (mDebug)
      std::cout << l << std::endl;

    if (tokens.size() > 7) {
      const time_t t(toTime_t(tokens[0]));
      if (t != -1) {
	const double height(toDouble(tokens[6]));
	if (finite(height)) {
	  const double feet(height * 100 / 2.54 / 12);
	  const double roundTo(0.1);
	  const double value(rint(feet / roundTo) * roundTo);
	  dumpToDatabase(mStation, DataDB::GAGE, t, value);
	}
      }
    }
    
    return true;
  }
}
