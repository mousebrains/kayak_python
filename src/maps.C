#include <Display.H>
#include <Convert.H>
#include <File.H>
#include <GIF.H>
#include <PageDB.H>
#include <cstdio>
#include <cerrno>
#include <sys/wait.h>
#include <iostream>

// Construct actual image

int
main (int argc,
      char **argv)
{
  ParameterDB params;
  const std::string dir(params.dirName("mapSourceDir"));
  const std::string webDir(params.dirName("webPageDir"));
  const std::string state("Oregon");

  Display display("MapBuilder",
                  "no_show is null and map_name is not null and state like '%" + state + "%'");

  const std::string clippedDir(dir + "/clipped/");
  const std::string backgroundMap(clippedDir + state + ".Background.gif");
  GIF gif(backgroundMap);

  if (!gif)
    return 0;

  for (Display::const_iterator it = display.begin(); it != display.end(); ++it) { 
    const Display::Record& rec(*it);
    if (!rec("status").empty()) {
      std::string color("blue");
      if (rec("status") == "Okay") color = "green";
      else if (rec("status") == "High") color = "red";
      else color = "yellow";

      const std::string filename(clippedDir + rec("map_name") + "." + color + ".gif");
      if (File::exists(filename))
        gif.comb(filename);
    } 
  }

  const std::string targetGif(webDir + "/" + state + ".gif");
  const std::string targetPNG(webDir + "/" + state + ".png");

  gif.dump(targetGif);
  gif.dumpPNG(targetPNG);

  PageDB page;
  page(File::tail(targetPNG), PageDB::FILE, "image/png", 3600, targetPNG);

  return 0;
}
