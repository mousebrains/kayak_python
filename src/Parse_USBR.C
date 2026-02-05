#include <Parse_USBR.H>
#include <File.H>
#include <String.H>
#include <Curl.H>
#include <iostream>
#include <cmath>

namespace Parsers {
  USBR::USBR(const Curl& curl,
             const bool qVerbose,
             const bool qDryRun,
	     DataDB& db)
    : Parse(curl.url(), qVerbose, qDryRun, db),
      mState(0)
  {
    serveUpCookedLines(curl.str()); 
  }

  bool
  USBR::line(const std::string& l)
  {
    typedef std::map<std::string, struct Info> tKnown;
    static const tKnown known = {
      {"Q",   Info(DataDB::FLOW)}, // Flow in CFS
      {"QC",  Info(DataDB::FLOW)}, // Canal discharge in CFS
      {"GH",  Info(DataDB::GAGE)}, // Gauge height in feet
      {"GH2", Info(DataDB::GAGE)}, // Gauge height in feet (Secondary)
      {"CH",  Info(DataDB::GAGE)}, // Canal Gauge height in feet
      {"WC",  Info(DataDB::TEMPERATURE, 32, 1.8)}, // Water temperature in celsius
      {"WF",  Info(DataDB::TEMPERATURE)}, // Water temperature in Farenheit
      {"WF2", Info(DataDB::TEMPERATURE)}, // Water temperature in Farenheit (Secondary)
      {"AF",  Info(DataDB::UNKNOWN)}, // acre feet of water storage capacity
      {"BH",  Info(DataDB::UNKNOWN)}, // Barometric pressure mmHg
      {"BV",  Info(DataDB::UNKNOWN)}, // Battery Voltage
      {"EH",  Info(DataDB::UNKNOWN)}, // error in height?
      {"FB",  Info(DataDB::UNKNOWN)}, // forebay elevation in feet
      {"GHG", Info(DataDB::UNKNOWN)}, // ??
      {"GPS", Info(DataDB::UNKNOWN)}, // ??
      {"HH",  Info(DataDB::UNKNOWN)}, // Height adjustment?
      {"HJ",  Info(DataDB::UNKNOWN)}, // Height adjustment?
      {"HK",  Info(DataDB::UNKNOWN)}, // Pool water surface elevation in feet
      {"NT",  Info(DataDB::UNKNOWN)}, // water total dissolved gass, mmHg
      {"OB",  Info(DataDB::UNKNOWN)}, // Instantaneous Air Temperature in Farenheit
      {"PC",  Info(DataDB::UNKNOWN)}, // Cummulative Precip inches
      {"QE",  Info(DataDB::UNKNOWN)}, // powerplant discharge unit 0
      {"QZ",  Info(DataDB::UNKNOWN)}, // powerplant discharge unit 1
      {"QT",  Info(DataDB::UNKNOWN)}, // total combined flow
      {"TV",  Info(DataDB::UNKNOWN)}, // valve house temperature in Farenheit
      {"VV",  Info(DataDB::UNKNOWN)}, // Power generation megawatts
      {"YR",  Info(DataDB::UNKNOWN)}, // ??
      {"ZS",  Info(DataDB::UNKNOWN)}, // Pump switch
    };

    if (mDebug)
      std::cout << mState << ' ' << l << std::endl;

    Tokenize tokens(l, ",", false);

    if (tokens.empty()) return true;

    if (String::trim(tokens[0]) == "BEGIN DATA") {
      mState = 1;
      return true;
    }

    if (String::trim(tokens[0]) == "END DATA") {
      mState = 0;
      return true;
    }

    if (mState == 0) return true; // Waiting for BEGIN DATA line

    if (tokens.size() < 2) { // Any other line needs at least two tokens
      mState = 0;
      std::cerr << "Unexpected line after BEGIN DATA\n" << l << std::endl;
      return false;
    }

    if (mState == 1) {
      {
        Tokenize fields(tokens[0]);
        if ((fields.size() != 2) ||
            (String::trim(fields[0]) != "DATE") ||
            (String::trim(fields[1]) != "TIME")) {
          mState = 0;
          std::cerr << "Expected a DATE TIME line\n" << l << std::endl;
          return false;
        } // if
      }
      mColumns.clear();
      for (Tokenize::size_type i = 1; i < tokens.size(); ++i) {
        const Tokenize fields(tokens[i]);
        if (fields.size() != 2) {
           mState = 0;
           std::cerr << "Not exactly two fields in header entry " << i << " '" << fields << "'\n"
                     << l << std::endl;
           return false;
        }
        const std::string stn(String::trim(fields[0]));
        const std::string code(String::trim(fields[1]));
        tKnown::const_iterator it(known.find(code));
        if (it == known.end()) { // Unknown code
           mState = 0;
           std::cerr << "Unknown data type, " << code << " for station " << stn << "\n" 
                     << l << std::endl;
           return false;
        } // if
        if (it->second.type != DataDB::UNKNOWN) {
          mColumns.insert(std::make_pair(i, Info(stn, it->second)));
        }
      } // for i
      mState = mColumns.empty() ? 0 : 2;
      return true;
    } // if mState == 1

    const time_t time(toDate(String::trim(tokens[0])));
    if (!time) 
      throw "Error converting '" + String::trim(tokens[0]) + "' into a date/time";
 
    for (tColumns::const_iterator it(mColumns.begin()), et(mColumns.end()); it != et; ++it) {
      const Tokenize::size_type index(it->first);
      const std::string& stn(it->second.station);
      const DataDB::TYPE code(it->second.type);
      const std::string str(index < tokens.size() ? String::trim(tokens[index]) : "");
      if (!str.empty()) {
        double value(it->second.normalize(toDouble(tokens[index])));
        if (finite(value) && ((value > 0) || (code == DataDB::GAGE))) {
          if ((code == DataDB::TEMPERATURE) && (value < 32) && (it->second.offset == 0)) {
            // Check if it might be C
            value = value * 1.8 + 32; // Convert to F from C
          }
          dumpToDatabase(stn, code, time, value);
        }
      } // if !empty
    } // iter
 
    return true;
  } //  line
} // namespace
