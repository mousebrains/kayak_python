#include <HTML.H>
#include <HTTP.H>
#include <CGI.H>
#include <iostream>

int 
main (int argc,
      char **argv)
{
  CGI cgi;

  HTML html(HTTP::compressable());

  html.head("Print Environment");

  html << "<H1>Args</H1>" << std::endl;
  html << "<OL>" << std::endl;
  for (int i = 0; i < argc; ++i)
    html << "<LI>" << argv[i] << std::endl;
  html << "</OL>" << std::endl;

  {
    extern char **environ;
 
    html << "<H1>Environ</H1>" << std::endl;
    html << "<OL>" << std::endl;
    for (int i = 0; environ[i]; ++i)
      html << "<LI>" << environ[i] << std::endl;
    html << "</OL>" << std::endl;
  }
 
  html << "<H1>CGI Path</H1>" << std::endl;
  html << "<OL>" << std::endl;
  for (CGI::size_type i = 0; i < cgi.nPath(); ++i)
    html << "<LI>" << cgi[i] << std::endl;
  html << "</OL>" << std::endl;

  html << "<H1>CGI Vars</H1>" << std::endl;
  html << "<UL>" << std::endl;
  for (CGI::const_iterator it = cgi.begin(); it != cgi.end(); ++it)
    html << "<LI>" << it->first << " -- " << it->second << std::endl;
  html << "</UL>" << std::endl;

 // html << "uid " << getuid() << " euid " << geteuid() << std::endl;

  HTTP http(std::cout, &html);
  http.content();
  http.modified(time(0));
  http.expires(time(0));

  return 0;
}
