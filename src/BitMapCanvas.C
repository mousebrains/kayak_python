#include <BitMapCanvas.H>
#include <Point.H>
#include <iostream>
#include <cmath>
#include <map>
#include <ft2build.h>
#include FT_FREETYPE_H

namespace {
  void doStep(int& x, int& y, const int deltax, const int deltay, 
              const Properties::FONTROTATION fr) {
    switch(fr) {
      case Properties::NOTSET:
      case Properties::STEPX: x += deltax; y += deltay; return;
      case Properties::STEPY: x -= deltay; y += deltax; return;
      case Properties::STEPmX: x -= deltax; y -= deltay; return;
      case Properties::STEPmY: x += deltay; y -= deltax; return;
    }
  }
}

BitMapCanvas::BitMapCanvas(const size_t width, 
			   const size_t height, 
			   const Properties& prop,
			   const size_t xdpi,
			   const size_t ydpi)
  : Canvas(prop, width, height),
    mMap(height, tRow(width, RGB::find(mProperties.background()))),
    mXdpi(xdpi),
    mYdpi(ydpi)
{
}

bool
BitMapCanvas::point(const int x,
                    const int y,
                    const RGB& rgb)
{
  if ((x >= 0) && ((tRow::size_type) x < mWidth) && 
      (y >= 0) && ((tMap::size_type) y < mHeight)) {
    mMap[y][x] = rgb;
    //    std::cout << "point " << x << "," << y << " -> " << rgb << std::endl;
  }

  return true;
}

bool
BitMapCanvas::point(const Point& pt,
		    const Properties& prop)
{
  const Properties p(mProperties | prop);
  const Point npt(p.transform() * pt);
  return point(npt.ix(), npt.iy(), RGB::find(prop.stroke()));
}

bool
BitMapCanvas::line(const Point& pt1,
		   const Point& pt2,
		   const Properties& prop)
{
  const Properties p(mProperties | prop);
  const RGB& rgb(RGB::find(p.stroke()));
  const Point npt1(p.transform() * pt1), npt2(p.transform() * pt2);

  const double y1(npt1.y()), y2(npt2.y());
  const double x1(npt1.x()), x2(npt2.x());

  if (std::isnan(y1) || std::isnan(y2) || std::isnan(x1) || std::isnan(x2) ||
      std::isinf(y1) || std::isinf(y2) || std::isinf(x1) || std::isinf(x2)) {
    return true;
  }

  if (fabs(x1 - x2) > fabs(y1 - y2)) { // step in x
    const double slope((y2 - y1) / (x2 - x1));
    const double intercept(y1 - slope * x1);
    const size_t nSteps((size_t) fabs(x2 - x1) + 1);
    const double delta((x2 - x1) < 0 ? -1 : 1);
    double x(x1);
    for (size_t i = 0; i < nSteps; ++i) {
	const tRow::size_type ix((size_t) x);
	if (ix < mWidth) { // in x range
	  const tMap::size_type iy((size_t) (intercept + slope * x));
	  if (iy < mHeight) // in y range
	    mMap[iy][ix] = rgb;
	}
	x += delta;
    }
  } else if (y1 == y2) { // A single point
    const tMap::size_type iy((size_t) y1);
    const tRow::size_type ix((size_t) x1);
    if ((iy < mHeight) && (ix < mWidth))
	mMap[iy][ix] = rgb;
  } else { // step in y
    const double slope((x2 - x1) / (y2 - y1));
    const double intercept(x1 - slope * y1);
    const size_t nSteps((size_t) fabs(y2 - y1) + 1);
    const double delta((y2 - y1) < 0 ? -1 : 1);
    double y(y1);
    for (size_t i = 0; i < nSteps; ++i) {
	const tMap::size_type iy((size_t) y);
	if (iy < mHeight) { // in y range
	  const tRow::size_type ix((size_t) (intercept + slope * y));
	  if (ix < mWidth) // in x range
            point(ix, iy, rgb);
	}
	y += delta;
    }

  }
  return true;
}

bool
BitMapCanvas::text(const std::string& text,
		   const Point& pt,
		   const Properties& prop)
{
  // std::cout << "text(" << text << ") @ " << pt << std::endl;

  static FT_Library library;  
  static bool initialized(false);

  if (!initialized) {
    initialized = true;
    const FT_Error error(FT_Init_FreeType(&library));
    if (error) {
	std::cerr << "Error initializing freetype library, " << error << std::endl;
	return false;
    }
  }

  push(prop);

  const Properties::FONTROTATION fr(mProperties.fontRotation());
  const RGB& rgb(RGB::find(mProperties.stroke()));
  const size_t fontSize(std::isnan(mProperties.fontSize()) ? 12 : ((size_t) mProperties.fontSize()));

  typedef std::map<std::string, FT_Face> tFaces;
  static tFaces faces;

  FT_Face face(0);

  {
    const std::string& fontFamily(mProperties.fontFamily());
    char buffer[16];
    snprintf(buffer, sizeof(buffer), "%lud", fontSize);
    const std::string key(fontFamily + "." + buffer);
    tFaces::const_iterator it(faces.find(key));
    if (it == faces.end()) {
	const std::string path("/home/tpw/local/share/fonts/truetype/ttf-bitstream-vera/");
	const std::string suffix(".ttf");
	const std::string font(path + (fontFamily.empty() ? "Vera" : fontFamily) + suffix);
	const FT_Error error(FT_New_Face(library, font.c_str(), 0, &face));
	if (error == FT_Err_Unknown_File_Format) {
	  std::cerr << "Error unrecognized font format, " << font << std::endl;
	  return false;
	} else if (error) {
	  std::cerr << "Error creating font face freetype, " << font << ", " << error << std::endl;
	  return false;
	}
	{
	  const FT_Error error(FT_Set_Char_Size(face, 0, fontSize * 64, mXdpi, mYdpi));
	  if (error) {
	    std::cerr << "Error setting char size, " << error << std::endl;
	    return false;
	  }

	  faces.insert(std::make_pair(key, face));
	}
    } else
	face = it->second;
  }

  const bool hasKerning(FT_HAS_KERNING(face));
  FT_UInt prevGlyphIndex(0);

  const Point npt(mProperties.transform() * pt);
  // std::cout << "npt " << npt << std::endl;

  int y(npt.iy());
  int x(npt.ix());

  const Properties p;

  size_t textOffset(0);
  {
    size_t textLength(0);

    for (std::string::size_type size(text.size()), i(0); i < size; ++i) {
      if (isspace(text[i])) {
        if ((i != 0) && !isspace(text[i-1]))
	  textLength += fontSize;
	continue;
      } 
      const FT_UInt glyphIndex(FT_Get_Char_Index(face, text[i]));
      if (!glyphIndex) {
	std::cerr << "Error getting glyphIndex for (" << text[i] << ")" << std::endl;
	return false;
      }
      {
	const FT_Error error(FT_Load_Glyph(face, glyphIndex, FT_LOAD_DEFAULT));
	if (error) {
	  std::cerr << "Error loading glyph, " << error << std::endl;
	  return false;
	}
      }
      
      FT_GlyphSlot slot(face->glyph);
      
      {
	const FT_Error error(FT_Render_Glyph(slot, FT_RENDER_MODE_NORMAL));
	if (error) {
	  std::cerr << "Error rendering glyph, " << error << std::endl;
	  return false;
	}
      }
      
      if (hasKerning && prevGlyphIndex) {
	FT_Vector delta;
	FT_Get_Kerning(face, prevGlyphIndex, glyphIndex, FT_KERNING_DEFAULT, &delta ); 
	textLength += delta.x >> 6;
      }
      prevGlyphIndex = glyphIndex;
      textLength += slot->advance.x >> 6;
    }
    
    const std::string& anchor(mProperties.fontAnchor());
    
    textOffset = ((anchor == "middle") ? (textLength / 2) :
		  (anchor == "start") ? 0 : textLength);
  }

  for (std::string::size_type size(text.size()), i(0); i < size; ++i) {
    if (isspace(text[i])) {
        if ((i != 0) && !isspace(text[i-1])) 
          doStep(x, y, fontSize, 0, fr);
	continue;
    } 

    const FT_UInt glyphIndex(FT_Get_Char_Index(face, text[i]));
    if (!glyphIndex) {
	std::cerr << "Error getting glyphIndex for (" << text[i] << ")" << std::endl;
	return false;
    }
    {
	const FT_Error error(FT_Load_Glyph(face, glyphIndex, FT_LOAD_DEFAULT));
	if (error) {
	  std::cerr << "Error loading glyph, " << error << std::endl;
	  return false;
	}
    }

    FT_GlyphSlot slot(face->glyph);

    {
	const FT_Error error(FT_Render_Glyph(slot, FT_RENDER_MODE_NORMAL));
	if (error) {
	  std::cerr << "Error rendering glyph, " << error << std::endl;
	  return false;
	}
    }

    if (hasKerning && prevGlyphIndex) {
	FT_Vector delta;
	FT_Get_Kerning(face, prevGlyphIndex, glyphIndex, FT_KERNING_DEFAULT, &delta ); 
        doStep(x, y, delta.x >> 6, 0, fr);
        // std::cout << "Kerning " << (delta.x >> 6) << std::endl;
    }
    prevGlyphIndex = glyphIndex;

    const FT_Bitmap& bitmap(slot->bitmap);
    unsigned char *ptr(bitmap.buffer);
    // std::cout << "char(" << text[i] << ") x " << x << " y " << y << std::endl;
    // std::cout << bitmap.rows << ' ' << bitmap.width << std::endl;
    for (size_t r = 0; r < bitmap.rows; ++r) {
        // printf("%3d", r);
	for (size_t c = 0; c < bitmap.width; ++c) {
          // printf (" %3d", ptr[c]);
	  if (ptr[c]) {
            int xx(x);
            int yy(y);
            int dx(c + slot->bitmap_left - 1 - textOffset);
            int dy(r - slot->bitmap_top + 1);
            doStep(xx, yy, dx, dy, fr);
            if ((xx >= 0) && ((tRow::size_type) xx < mWidth) &&
                (yy >= 0) && ((tMap::size_type) yy < mHeight)) {
              if (ptr[c] == 255) // Max color so just set
	        point(xx, yy, rgb);
              else { // not max color, so scale
                const RGB& b(mMap[yy][xx]); // Current color
                const double factor((double) ptr[c] / 255.);
	        point(xx, yy, rgb * factor + b * (1 - factor));
              }
	      // std::cout << xx << " " << yy << std::endl;
            }
	  }
	}
        // printf ("\n");
	ptr += bitmap.width;
    }
    doStep(x, y, slot->advance.x >> 6, 0, fr);  
  }

  pop();

  return true;
}

std::ostream&
operator << (std::ostream& os,
	       const BitMapCanvas& bm)
{
  for (BitMapCanvas::tMap::const_iterator et(bm.mMap.end()), it(bm.mMap.begin()); it != et; ++it) {
    std::string space;
    for (BitMapCanvas::tRow::const_iterator jet(it->end()), jt(it->begin()); jt != jet; ++jt) {
	os << space << *jt;
	space = ", ";
    }
    os << std::endl;
  }
  return os;
}
