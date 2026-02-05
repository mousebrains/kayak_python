#include <RGB.H>
#include <Stroke.H>
#include <iostream>
#include <map>

namespace {
  typedef std::map<std::string, RGB> tColors;
  tColors colors;

  void initColors() {
    if (!colors.empty())
      return;
    colors.insert(std::make_pair("aliceblue", RGB(240, 248, 255)));
    colors.insert(std::make_pair("antiquewhite", RGB(250, 235, 215)));
    colors.insert(std::make_pair("aqua", RGB( 0, 255, 255)));
    colors.insert(std::make_pair("aquamarine", RGB(127, 255, 212)));
    colors.insert(std::make_pair("azure", RGB(240, 255, 255)));
    colors.insert(std::make_pair("beige", RGB(245, 245, 220)));
    colors.insert(std::make_pair("bisque", RGB(255, 228, 196)));
    colors.insert(std::make_pair("black", RGB( 0, 0, 0)));
    colors.insert(std::make_pair("blanchedalmond", RGB(255, 235, 205)));
    colors.insert(std::make_pair("blue", RGB( 0, 0, 255)));
    colors.insert(std::make_pair("blueviolet", RGB(138, 43, 226)));
    colors.insert(std::make_pair("brown", RGB(165, 42, 42)));
    colors.insert(std::make_pair("burlywood", RGB(222, 184, 135)));
    colors.insert(std::make_pair("cadetblue", RGB( 95, 158, 160)));
    colors.insert(std::make_pair("chartreuse", RGB(127, 255, 0)));
    colors.insert(std::make_pair("chocolate", RGB(210, 105, 30)));
    colors.insert(std::make_pair("coral", RGB(255, 127, 80)));
    colors.insert(std::make_pair("cornflowerblue", RGB(100, 149, 237)));
    colors.insert(std::make_pair("cornsilk", RGB(255, 248, 220)));
    colors.insert(std::make_pair("crimson", RGB(220, 20, 60)));
    colors.insert(std::make_pair("cyan", RGB( 0, 255, 255)));
    colors.insert(std::make_pair("darkblue", RGB( 0, 0, 139)));
    colors.insert(std::make_pair("darkcyan", RGB( 0, 139, 139)));
    colors.insert(std::make_pair("darkgoldenrod", RGB(184, 134, 11)));
    colors.insert(std::make_pair("darkgray", RGB(169, 169, 169)));
    colors.insert(std::make_pair("darkgreen", RGB( 0, 100, 0)));
    colors.insert(std::make_pair("darkgrey", RGB(169, 169, 169)));
    colors.insert(std::make_pair("darkkhaki", RGB(189, 183, 107)));
    colors.insert(std::make_pair("darkmagenta", RGB(139, 0, 139)));
    colors.insert(std::make_pair("darkolivegreen", RGB( 85, 107, 47)));
    colors.insert(std::make_pair("darkorange", RGB(255, 140, 0)));
    colors.insert(std::make_pair("darkorchid", RGB(153, 50, 204)));
    colors.insert(std::make_pair("darkred", RGB(139, 0, 0)));
    colors.insert(std::make_pair("darksalmon", RGB(233, 150, 122)));
    colors.insert(std::make_pair("darkseagreen", RGB(143, 188, 143)));
    colors.insert(std::make_pair("darkslateblue", RGB( 72, 61, 139)));
    colors.insert(std::make_pair("darkslategray", RGB( 47, 79, 79)));
    colors.insert(std::make_pair("darkslategrey", RGB( 47, 79, 79)));
    colors.insert(std::make_pair("darkturquoise", RGB( 0, 206, 209)));
    colors.insert(std::make_pair("darkviolet", RGB(148, 0, 211)));
    colors.insert(std::make_pair("deeppink", RGB(255, 20, 147)));
    colors.insert(std::make_pair("deepskyblue", RGB( 0, 191, 255)));
    colors.insert(std::make_pair("dimgray", RGB(105, 105, 105)));
    colors.insert(std::make_pair("dimgrey", RGB(105, 105, 105)));
    colors.insert(std::make_pair("dodgerblue", RGB( 30, 144, 255)));
    colors.insert(std::make_pair("firebrick", RGB(178, 34, 34)));
    colors.insert(std::make_pair("floralwhite", RGB(255, 250, 240)));
    colors.insert(std::make_pair("forestgreen", RGB( 34, 139, 34)));
    colors.insert(std::make_pair("fuchsia", RGB(255, 0, 255)));
    colors.insert(std::make_pair("gainsboro", RGB(220, 220, 220)));
    colors.insert(std::make_pair("ghostwhite", RGB(248, 248, 255)));
    colors.insert(std::make_pair("gold", RGB(255, 215, 0)));
    colors.insert(std::make_pair("goldenrod", RGB(218, 165, 32)));
    colors.insert(std::make_pair("gray", RGB(128, 128, 128)));
    colors.insert(std::make_pair("grey", RGB(128, 128, 128)));
    colors.insert(std::make_pair("green", RGB( 0, 128, 0)));
    colors.insert(std::make_pair("greenyellow", RGB(173, 255, 47)));
    colors.insert(std::make_pair("honeydew", RGB(240, 255, 240)));
    colors.insert(std::make_pair("hotpink", RGB(255, 105, 180)));
    colors.insert(std::make_pair("indianred", RGB(205, 92, 92)));
    colors.insert(std::make_pair("indigo", RGB( 75, 0, 130)));
    colors.insert(std::make_pair("ivory", RGB(255, 255, 240)));
    colors.insert(std::make_pair("khaki", RGB(240, 230, 140)));
    colors.insert(std::make_pair("lavender", RGB(230, 230, 250)));
    colors.insert(std::make_pair("lavenderblush", RGB(255, 240, 245)));
    colors.insert(std::make_pair("lawngreen", RGB(124, 252, 0)));
    colors.insert(std::make_pair("lemonchiffon", RGB(255, 250, 205)));
    colors.insert(std::make_pair("lightblue", RGB(173, 216, 230)));
    colors.insert(std::make_pair("lightcoral", RGB(240, 128, 128)));
    colors.insert(std::make_pair("lightcyan", RGB(224, 255, 255)));
    colors.insert(std::make_pair("lightgoldenrodyellow", RGB(250, 250, 210)));
    colors.insert(std::make_pair("lightgray", RGB(211, 211, 211)));
    colors.insert(std::make_pair("lightgreen", RGB(144, 238, 144)));
    colors.insert(std::make_pair("lightgrey", RGB(211, 211, 211)));
    colors.insert(std::make_pair("lightpink", RGB(255, 182, 193)));
    colors.insert(std::make_pair("lightsalmon", RGB(255, 160, 122)));
    colors.insert(std::make_pair("lightseagreen", RGB( 32, 178, 170)));
    colors.insert(std::make_pair("lightskyblue", RGB(135, 206, 250)));
    colors.insert(std::make_pair("lightslategray", RGB(119, 136, 153)));
    colors.insert(std::make_pair("lightslategrey", RGB(119, 136, 153)));
    colors.insert(std::make_pair("lightsteelblue", RGB(176, 196, 222)));
    colors.insert(std::make_pair("lightyellow", RGB(255, 255, 224)));
    colors.insert(std::make_pair("lime", RGB( 0, 255, 0)));
    colors.insert(std::make_pair("limegreen", RGB( 50, 205, 50)));
    colors.insert(std::make_pair("linen", RGB(250, 240, 230)));
    colors.insert(std::make_pair("magenta", RGB(255, 0, 255)));
    colors.insert(std::make_pair("maroon", RGB(128, 0, 0)));
    colors.insert(std::make_pair("mediumaquamarine", RGB(102, 205, 170)));
    colors.insert(std::make_pair("mediumblue", RGB( 0, 0, 205)));
    colors.insert(std::make_pair("mediumorchid", RGB(186, 85, 211)));
    colors.insert(std::make_pair("mediumpurple", RGB(147, 112, 219)));
    colors.insert(std::make_pair("mediumseagreen", RGB( 60, 179, 113)));
    colors.insert(std::make_pair("mediumslateblue", RGB(123, 104, 238)));
    colors.insert(std::make_pair("mediumspringgreen", RGB( 0, 250, 154)));
    colors.insert(std::make_pair("mediumturquoise", RGB( 72, 209, 204)));
    colors.insert(std::make_pair("mediumvioletred", RGB(199, 21, 133)));
    colors.insert(std::make_pair("midnightblue", RGB( 25, 25, 112)));
    colors.insert(std::make_pair("mintcream", RGB(245, 255, 250)));
    colors.insert(std::make_pair("mistyrose", RGB(255, 228, 225)));
    colors.insert(std::make_pair("moccasin", RGB(255, 228, 181)));
    colors.insert(std::make_pair("navajowhite", RGB(255, 222, 173)));
    colors.insert(std::make_pair("navy", RGB( 0, 0, 128)));
    colors.insert(std::make_pair("oldlace", RGB(253, 245, 230)));
    colors.insert(std::make_pair("olive", RGB(128, 128, 0)));
    colors.insert(std::make_pair("olivedrab", RGB(107, 142, 35)));
    colors.insert(std::make_pair("orange", RGB(255, 165, 0)));
    colors.insert(std::make_pair("orangered", RGB(255, 69, 0)));
    colors.insert(std::make_pair("orchid", RGB(218, 112, 214)));
    colors.insert(std::make_pair("palegoldenrod", RGB(238, 232, 170)));
    colors.insert(std::make_pair("palegreen", RGB(152, 251, 152)));
    colors.insert(std::make_pair("paleturquoise", RGB(175, 238, 238)));
    colors.insert(std::make_pair("palevioletred", RGB(219, 112, 147)));
    colors.insert(std::make_pair("papayawhip", RGB(255, 239, 213)));
    colors.insert(std::make_pair("peachpuff", RGB(255, 218, 185)));
    colors.insert(std::make_pair("peru", RGB(205, 133, 63)));
    colors.insert(std::make_pair("pink", RGB(255, 192, 203)));
    colors.insert(std::make_pair("plum", RGB(221, 160, 221)));
    colors.insert(std::make_pair("powderblue", RGB(176, 224, 230)));
    colors.insert(std::make_pair("purple", RGB(128, 0, 128)));
    colors.insert(std::make_pair("red", RGB(255, 0, 0)));
    colors.insert(std::make_pair("rosybrown", RGB(188, 143, 143)));
    colors.insert(std::make_pair("royalblue", RGB( 65, 105, 225)));
    colors.insert(std::make_pair("saddlebrown", RGB(139, 69, 19)));
    colors.insert(std::make_pair("salmon", RGB(250, 128, 114)));
    colors.insert(std::make_pair("sandybrown", RGB(244, 164, 96)));
    colors.insert(std::make_pair("seagreen", RGB( 46, 139, 87)));
    colors.insert(std::make_pair("seashell", RGB(255, 245, 238)));
    colors.insert(std::make_pair("sienna", RGB(160, 82, 45)));
    colors.insert(std::make_pair("silver", RGB(192, 192, 192)));
    colors.insert(std::make_pair("skyblue", RGB(135, 206, 235)));
    colors.insert(std::make_pair("slateblue", RGB(106, 90, 205)));
    colors.insert(std::make_pair("slategray", RGB(112, 128, 144)));
    colors.insert(std::make_pair("slategrey", RGB(112, 128, 144)));
    colors.insert(std::make_pair("snow", RGB(255, 250, 250)));
    colors.insert(std::make_pair("springgreen", RGB( 0, 255, 127)));
    colors.insert(std::make_pair("steelblue", RGB( 70, 130, 180)));
    colors.insert(std::make_pair("tan", RGB(210, 180, 140)));
    colors.insert(std::make_pair("teal", RGB( 0, 128, 128)));
    colors.insert(std::make_pair("thistle", RGB(216, 191, 216)));
    colors.insert(std::make_pair("tomato", RGB(255, 99, 71)));
    colors.insert(std::make_pair("turquoise", RGB( 64, 224, 208)));
    colors.insert(std::make_pair("violet", RGB(238, 130, 238)));
    colors.insert(std::make_pair("wheat", RGB(245, 222, 179)));
    colors.insert(std::make_pair("white", RGB(255, 255, 255)));
    colors.insert(std::make_pair("whitesmoke", RGB(245, 245, 245)));
    colors.insert(std::make_pair("yellow", RGB(255, 255, 0)));
    colors.insert(std::make_pair("yellowgreen", RGB(154, 205, 50)));
  }
}

const RGB &
RGB::find(const std::string& str) 
{
  initColors();

  tColors::const_iterator it(colors.find(str));

  if (it != colors.end())
    return it->second; 
  
  std::cerr << "RGB not found for color(" << str << ")" << std::endl;
  return colors.begin()->second;
}

const RGB &
RGB::find(const Stroke& stroke)
{
  return find(stroke.stroke());
}

RGB& 
RGB::operator  +=  (const RGB& rhs)
{
  mRGB = composite(red() + rhs.red(), green() + rhs.green(), blue() + rhs.blue());
  return *this;
}

RGB 
RGB::operator  +  (const RGB& rhs) const
{
  return RGB(red() + rhs.red(), green() + rhs.green(), blue() + rhs.blue());
}

RGB& 
RGB::operator  -=  (const RGB& rhs)
{
  mRGB = composite(red() - rhs.red(), green() - rhs.green(), blue() - rhs.blue());
  return *this;
}

RGB 
RGB::operator  -  (const RGB& rhs) const
{
  return RGB(red() - rhs.red(), green() - rhs.green(), blue() - rhs.blue());
}

RGB& 
RGB::operator  *=  (const double f)
{
  mRGB = composite((Component) (red() * f), (Component) (green() * f), (Component) (blue() * f));
  return *this;
}

RGB 
RGB::operator  *  (const double f) const
{
  return RGB((Component) (red() * f), (Component) (green() * f), (Component) (blue() * f));
}

RGB& 
RGB::operator  /=  (const double f)
{
  *this *= 1/f;
  return *this;
}

RGB 
RGB::operator  /  (const double f) const
{
  return *this * (1 / f);
}

std::ostream&
operator << (std::ostream& os,
	     const RGB& rgb)
{
  std::string name;

  for (tColors::const_iterator it = colors.begin(); it != colors.end(); ++it) 
    if (rgb == it->second) {
      name = it->first;
      break;
    }

  os << "('" << name << "'," 
     << (unsigned int) rgb.red() << ',' 
     << (unsigned int) rgb.green() << ',' 
     << (unsigned int) rgb.blue() << ')';

  return os;
}
