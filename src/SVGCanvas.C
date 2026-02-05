#include <SVGCanvas.H>
#include <Point.H>
#include <Points.H>
#include <iostream>
#include <sstream>
#include <cstdlib>

SVGCanvas::SVGCanvas(const std::string& description,
                     const std::string& width, 
		     const std::string& height, 
		     const Properties& prop)
  : Canvas(prop, strtod(width.c_str(), 0), strtod(height.c_str(), 0))
{
  mOS << "<?xml version='1.0' standalone='no'?>" << std::endl
     << "<!DOCTYPE svg PUBLIC '-//W3C//DTD SVG 1.1//EN'" << std::endl
     << " 'http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd'>" << std::endl
     << "<svg width='" << width << "' height='" << height << "'"
     << " " << mProperties.svgXML()
     << " version='1.1' xmlns='http://www.w3.org/2000/svg'>" << std::endl;

  if (!description.empty()) 
    mOS << "<desc>" << description << "</desc>" << std::endl;
}

SVGCanvas::~SVGCanvas()
{
}

bool
SVGCanvas::close()
{
  if (!mContents.empty())
    return false;

  for (tStack::size_type i = 0; i < mStack.size(); ++i)
    mOS << "</g>" << std::endl;

  mOS << "</svg>" << std::endl;

  mContents = mOS.str();

  return true;
}

bool
SVGCanvas::maybePush(const Properties& prop)
{
  const std::string xml(prop.svgXML(mProperties));

  if (xml.empty())
    return false;

  mOS << "<g " << xml << ">" << std::endl;
  Canvas::push(prop);
  return true;
}

void
SVGCanvas::push(const Properties& prop)
{
  const std::string xml(prop.svgXML(mProperties));

  if (xml.empty())
    mOS << "<g>" << std::endl;
  else
    mOS << "<g " << xml << ">" << std::endl;

  Canvas::push(prop);
}

bool
SVGCanvas::pop()
{
  if (Canvas::pop()) {
    mOS << "</g>" << std::endl;
    return true;
  }
  return false;
}

bool
SVGCanvas::line(const Point& pt1,
		const Point& pt2,
		const Properties& prop)
{
  mOS << "<line"
	<< " x1='" << pt1.x() << "' y1='" << pt1.y() << "'"
	<< " x2='" << pt2.x() << "' y2='" << pt2.y() << "'";

  const std::string xml(prop.svgXML(mProperties));

  if (!xml.empty())
    mOS << " " << xml;

  mOS << " />" << std::endl;

  return true;
}

bool
SVGCanvas::rectangle(const Point& pt1,
		     const Point& pt2,
		     const Properties& prop)
{
  mOS << "<rect"
	<< " x='" << pt1.x() << "' y='" << pt1.y() << "'"
	<< " width='" << (pt2.x() - pt1.x()) << "' height='" << (pt2.y() - pt1.y()) << "'";

  if (prop != mProperties)
    mOS << " " << prop.svgXML(mProperties);

  mOS << " />" << std::endl;

  return true;
}

bool
SVGCanvas::polyline(const Points& pts,
		    const Properties& prop)
{
  mOS << "<polyline";

  if (prop != mProperties)
    mOS << " " << prop.svgXML(mProperties);

  mOS << " points='";
  std::string space;
  for (Points::const_iterator it = pts.begin(); it != pts.end(); ++it) {
    mOS << space << it->x() << "," << it->y();
    space = " ";
  }
  mOS << "' />" << std::endl;

  return true;
}

bool
SVGCanvas::text(const std::string& text,
		const Point& pt,
		const Properties& prop)
{
  Properties p(prop);

  if ((pt.x() != 0) || (pt.y() != 0))
    p.translate(pt.x(), pt.y());
  p.scale(1, -1);
  
  const std::string xml(p.svgXML(mProperties));

  mOS << "<text" << " x='0' y='0'" 
      << (xml.empty() ? "" : " ") << xml 
      << ">" << text << "</text>"
      << std::endl;

  return true;
}

size_t
SVGCanvas::size()
{
  if (mContents.empty())
    close();

  return mContents.size();
}

const std::string&
SVGCanvas::str()
{
  if (mContents.empty())
    close();

  return mContents;
}

std::ostream&
operator << (std::ostream& os,
             SVGCanvas& c)
{
  if (c.mContents.empty())
    c.close();

  os << c.mContents;
  return os;
}
