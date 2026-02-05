BEGIN {
  prefix = "file:///home/tpw/tools/kayaking/pages"

  suffixes["agno3"] = "_hgirg.xml"
  suffixes["apro3"] = "_hgirg.xml"
  suffixes["arwo3"] = "_hgirg.xml"
  suffixes["azao3"] = "_hgirz.xml"
  suffixes["bcko3"] = "_hgirr.xml"
  suffixes["bclo3"] = "_hgirr.xml"
  suffixes["beao3"] = "_hgirp.xml"
  suffixes["bluo3"] = "_hgirr.xml"
  suffixes["bono3"] = "_hgirp.xml"
  suffixes["brbo3"] = "_hgirr.xml"
  suffixes["btyo3"] = "_hgirg.xml"
  suffixes["buro3"] = "" # Dead
  suffixes["cbao3"] = "_hgirp.xml"
  suffixes["cgro3"] = "_hgirp.xml"
  suffixes["chto3"] = "_hgirg.xml"
  suffixes["coco3"] = "_hgirp.xml"
  suffixes["cogo3"] = "_hgirz.xml"
  suffixes["coqo3"] = "_hgirp.xml"
  suffixes["coro3"] = "_hgirp.xml"
  suffixes["cwmo3"] = "_hgirz.xml"
  suffixes["ecdo3"] = "_hgirz.xml"
  suffixes["eglo3"] = "_hgirg.xml"
  suffixes["ekto3"] = "_hgirp.xml"
  suffixes["elko3"] = "_hgirg.xml"
  suffixes["esto3"] = "_hgirr.xml"
  suffixes["falo3"] = "_hgirr.xml"
  suffixes["fnno3"] = "_hgirp.xml"
  suffixes["frmo3"] = "_hgirr.xml"
  suffixes["frno3"] = "_hgirr.xml"
  suffixes["ftjc1"] = "_hgirg.xml"
  suffixes["gaso3"] = "_hgirg.xml"
  suffixes["glno3"] = "_hgirg.xml"
  suffixes["grao3"] = "_hgirg.xml"
  suffixes["hapc1"] = "_hgirg.xml"
  suffixes["hcro3"] = "_hgirr.xml"
  suffixes["irgc1"] = "_hgirg.xml"
  suffixes["krbo3"] = "_hgirg.xml"
  suffixes["kelw1"] = "_hgirr.xml"
  suffixes["loco3"] = "_hgirr.xml"
  suffixes["lrww1"] = "_hgirp.xml"
  suffixes["lsmo3"] = "_hgirr.xml"
  suffixes["mczo3"] = "_hgirp.xml"
  suffixes["merw1"] = "_hgirp.xml"
  suffixes["mfdo3"] = "_hgirg.xml"
  suffixes["miwo3"] = "_hgirp.xml"
  suffixes["mklo3"] = "_hgirp.xml"
  suffixes["mono3"] = "_hgirp.xml"
  suffixes["myno3"] = "_hgirp.xml"
  suffixes["mypo3"] = "_hgirp.xml"
  suffixes["nebo3"] = "" # Dead
  suffixes["oako3"] = "_hgirr.xml"
  suffixes["ocuo3"] = "_hgirr.xml"
  suffixes["orco3"] = "_hgirr.xml"
  suffixes["powo3"] = "_hgirg.xml"
  suffixes["prto3"] = "_hgirr.xml"
  suffixes["puwo3"] = "_hgirp.xml"
  suffixes["qcco3"] = "_hgirr.xml"
  suffixes["rdlo3"] = "_hgirp.xml"
  suffixes["rsbo3"] = "_hgirp.xml"
  suffixes["rygo3"] = "_hgirp.xml"
  suffixes["sbrc1"] = "_hgirg.xml"
  suffixes["scro3"] = "_hgirg.xml"
  suffixes["seic1"] = "_hgirg.xml"
  suffixes["shno3"] = "_hgirp.xml"
  suffixes["silo3"] = "_hgirp.xml"
  suffixes["skaw1"] = "_hgirp.xml"
  suffixes["smcw1"] = "_hgirp.xml"
  suffixes["ssco3"] = "_hgirr.xml"
  suffixes["ssfo3"] = "_hgirr.xml"
  suffixes["suro3"] = "_hgirz.xml"
  suffixes["suvo3"] = "_hgirr.xml"
  suffixes["syco3"] = "_hgirp.xml"
  suffixes["tido3"] = "_hgirp.xml"
  suffixes["tilo3"] = "_hgirg.xml"
  suffixes["tlyo3"] = "_hgirp.xml"
  suffixes["tsfw1"] = "_hgirr.xml"
  suffixes["vanw1"] = "_hgirp.xml"
  suffixes["wauo3"] = "_hgirp.xml"
  suffixes["wino3"] = "_hgirz.xml"
  suffixes["wmso3"] = "_hgirg.xml"
  suffixes["wnro3"] = "_hgirg.xml"
  suffixes["wsno3"] = "_hgirg.xml"
  suffixes["yrec1"] = "_hgirg.xml"
  suffixes["default"] = "_hgirg.xml"
}

/[[:space:]]*#/ {
  next;
}

{
  parser = $1
  url = $2
  hours = $3

  if (parser != "" && url != "") {
    if (parser == "usgs.rdb") { parser = "usgs"}
    if (parser == "noaa2") { 
      n = split(url, a, "=")
      id = a[n]
      if (id in suffixes) {
        suffix = suffixes[id]
      } else {
        suffix = suffixes["default"]
      }
      if (suffix != "") {
        parser = "noaa.xml"
        url = "http://ahps2.wrh.noaa.gov/ahps2/xml/" id suffix
      } else {
        next
      }
    }
    # if (substr(url, 1, 6) == "http:/") { url = prefix substr(url, 7); }
    # if (substr(url, 1, 7) == "https:/") { url = prefix substr(url, 8); }
    # if (substr(url, 1, 5) == "ftp:/") { url = prefix substr(url, 6); }
    printf ("insert into URLparse values ('%s', '%s', '%s', null);\n", url, parser, hours);
  }
}
