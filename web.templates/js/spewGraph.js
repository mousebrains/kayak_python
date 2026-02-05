function spewGraph(id, suffix, width, height) {
  var prefix = "/cgi/"

  if (navigator.mimeTypes == null) { // No mime types, so say to use a png version
    document.getElementById(id).innerHTML = '<img src="' + prefix + "png" + suffix + '" />';
  } else if (navigator.mimeTypes.length <= 0) { // Probably IE
    try { // Check for Adobe SVG support
      var qSVG = new ActiveXObject("Adobe.SVGCtl");
      document.getElementById(id).innerHTML = 
          '<embed src="' + prefix + "svg" + suffix + 
          '" type="image/svg+xml" width="' + width + '" height="' + height + '" />';
    } catch (e) { // No Adobe SVG support
      document.getElementById(id).innerHTML = '<img src="' + prefix + "png" + suffix + '" />';
    }
  } else if (navigator.mimeTypes["image/svg+xml"] != null) { // Has SVG support
    document.getElementById(id).innerHTML = 
 	'<object data="' + prefix + "svg" + suffix + '" type="image/svg+xml" width="' + 
 	width + '" height="' + height + '" />';
  } else { // No SVG support
    document.getElementById(id).innerHTML =
	'<object data="' + prefix + "png" + suffix + 
	'" type="image/png" width="' + width + '" height="' + height + '" />';
  }
}
