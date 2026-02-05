#ifndef INC_Parse_Parse_H_
#define INC_Parse_Parse_H_

#include <DataDB.H>
#include <string>

#ifndef _XOPEN_SOURCE
#define _XOPEN_SOURCE
#define INC_Parse_Parse_UNDO
#endif

#include <ctime>

#ifdef INC_Parse_Parse_UNDO
#undef INC_Parse_Parse_UNDO
#undef _XOPEN_SOURCE
#endif

namespace Parsers {
class Parse {
protected:
  DataDB& mData;
  const std::string mURL;
  mutable int mDBupdates;
  bool mDebug;
  bool mDryRun;
  bool mqWarn;
  bool mqDumpContent;
  std::string mText; // Text feed from serveUp

  void serveUpCookedLines(const std::string& l);
  void serveUpLines(const std::string& l);

  int toInt(const std::string& text, 
	    const bool useAll = false, 
	    const bool warn = false) const;

  time_t toTime_t(const std::string& text,
		  const bool useAll = false,
		  const bool warn = false) const;
  
  double toDouble(const std::string& text, 
		  const bool useAll = false, 
		  const bool warn = false) const;

  time_t toDate(const std::string& text,
		const bool warn = false);

  time_t toDate(const std::string& text,
                const std::string& timezone,
		const bool warn = false);

  bool dumpToDatabase(const std::string& station,
		      const DataDB::TYPE type,
		      const time_t when,
		      const double& value);

  bool dumpToDatabase(const std::string& station,
		      const std::string& type,
		      const time_t when,
		      const double& value);

  virtual bool line(const std::string& l) = 0;

  Parse(const std::string& url, 
	const bool qVerbose, 
	const bool qDryRun,
        DataDB& db);
public:
  virtual ~Parse() {mData.wrapup();}

  virtual const char *name() const = 0;
};
}

#endif // INC_Parse_Parse_H_
