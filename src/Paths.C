#include <Paths.H>

namespace Paths {
  const std::string MaintainerName("Pat Welch");
  const std::string Maintainer("<a href=\"mailto:pat.kayak@gmail.com\">" +
		               MaintainerName + "</a>");
  const std::string PageArchiveRoot("/home/tpw/page.archive");
  const std::string URL("http://levels.wkcc.org");

  // const std::string DocumentRoot("/levels");
  const std::string DocumentRoot("/");
  const std::string CGIRoot(DocumentRoot + "cgi/");
  const std::string JSRoot(DocumentRoot + "js/");

  const std::string MySQLUser("levels");
  const std::string MySQLPasswd("Deschutes");
  const std::string MySQLHost("mysql.wkcc.dreamhosters.com");
  const std::string MySQLInfoDB("levels_information");
  const std::string MySQLPageDB("levels_page");
  const std::string MySQLDataDB("levels_data");
}
