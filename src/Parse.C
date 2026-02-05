#include <Parse.H>
#include <pstream.H>
#include <HTMLrender.H>
#include <Env.H>
#include <Convert.H>
#include <sstream>
#include <iostream>
#include <cerrno>

namespace Parsers {
  Parse::Parse(const std::string& url, 
	       const bool qVerbose, 
	       const bool qDryRun,
	       DataDB& db)
    : mData(db)
    , mURL(url)
    , mDBupdates(0)
    , mDebug(qVerbose || Env::get("qDebug"))
    , mDryRun(qDryRun)
    , mqWarn(true)
    , mqDumpContent(true)
  {
  }

  void
  Parse::serveUpCookedLines(const std::string& text)
  {
    HTMLrender r(text);
    serveUpLines(r.str());
  }

  void
  Parse::serveUpLines(const std::string& text)
  {
    mText = text;
    std::istringstream iss(text);

    mDBupdates = 0;
    mqWarn = true;
    mqDumpContent = false;

    for (std::string l; getline(iss, l);) {
      for (std::string::size_type i = 0; (i = l.find('\r', i)) != l.npos;)
	l.replace(i, 1, "");
      if (!line(l))
	break;
    }
    
    if (mqWarn && !mDBupdates)
      std::cerr << "WARNING: No database updates from " 
                << mURL << " parser(" << name() << ")" << std::endl
                << mText << std::endl;

    if (mqDumpContent)
      std::cerr << "Content of " << mURL << " parser(" << name() << ")\n"
                << mText << std::endl;

    mText.clear();
  }

  int 
  Parse::toInt(const std::string& text, 
	       const bool useAll,
	       const bool warn) const
  {
    int n;
    std::istringstream iss(text);

    if ((iss >> n)) {
      if (useAll && iss.eof())
	std::cout << "Did not use all of '" << text << "' when converting to an integer for "
		  << mURL << std::endl;
      return n;
    }
    if (warn)
      std::cerr << "Error converting '" << text << "' into an integer" << std::endl;
    return 0;
  }
  
  time_t
  Parse::toTime_t(const std::string& text, 
		  const bool useAll,
		  const bool warn) const
  {
    time_t val;
    std::istringstream iss(text);

    if ((iss >> val)) {
      if (useAll && !iss.eof()) 
	std::cout << "Did not use all of '" << text << "' when converting to a time_t for "
		  << mURL << std::endl;
      return val;
    }
    if (warn)
      std::cerr << "Error converting '" << text << "' into an double" << std::endl;
    return -1;
  }
  
  double 
  Parse::toDouble(const std::string& text, 
		  const bool useAll,
		  const bool warn) const
  {
    double val;
    std::istringstream iss(text);

    if ((iss >> val)) {
      if (useAll && !iss.eof())
	std::cout << "Did not use all of '" << text << "' when converting to a double for "
		  << mURL 
                  << std::endl;
      return val;
    }
    if (warn)
      std::cerr << "Error converting '" << text << "' into an double" << std::endl;
    return strtod("INFINITY", 0);
  }

  time_t
  Parse::toDate(const std::string& text,
		const bool warn)
  {
    const time_t t(Convert::toTime(text));

    if ((t == -1) && warn)
      std::cerr << "Error converting '" << text << "' to a date/time for " << mURL << std::endl;

    return t;
  }

  time_t
  Parse::toDate(const std::string& text,
                const std::string& timezone,
		const bool warn)
  {
    const time_t t(Convert::toTime(text, timezone));

    if ((t == -1) && warn)
      std::cerr << "Error converting '" << text << "' tz '" << timezone 
                << "' to a date/time for " << mURL << std::endl;

    return t;
  }

  bool 
  Parse::dumpToDatabase(const std::string& station,
			const DataDB::TYPE type,
			const time_t when,
			const double& value)
  {
    ++mDBupdates;

    if (!mDryRun) {
      if (mData(station, when, type, value)) {
        mData.url(mURL, station);
      } else {
        mqDumpContent = true;
	std::cout << "dumpToDatabase failed for " << station << "/" 
		  << type << " " << value << " " << Convert::toString(when)
		  << " " << mURL
                  << " " << name()
		  << std::endl << mText << std::endl;
      }
    }

    if (mDebug)
      std::cout << "DB dump " << station << "/" << type << " " << value 
		<< " " << Convert::toString(when)
		<< std::endl;
    return true;
  }

  bool 
  Parse::dumpToDatabase(const std::string& station,
			const std::string& type,
			const time_t when,
			const double& value)
  {
    ++mDBupdates;

    if (!mDryRun) {
      if (mData(station, when, type, value)) {
        mData.url(mURL, station);
      } else {
        mqDumpContent = true;
	std::cout << "dumpToDatabase failed for " << station << "/" 
		  << type << " " << value << " " << Convert::toString(when)
		  << " " << mURL
                  << " " << name()
		  << std::endl << mText << std::endl;
      }
    }

    if (mDebug)
      std::cout << "DB dump " << station << "/" << type << " " << value 
		<< " " << Convert::toString(when)
		<< std::endl;
    return true;
  }
}
