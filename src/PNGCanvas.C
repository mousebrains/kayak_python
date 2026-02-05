#include <PNGCanvas.H>
#include <RGB.H>
#include <map>
#include <iostream>
#include <sstream>
#include <png.h>
#include <zlib.h>
#include <cstring>

void
userWriteData(png_structp png_ptr,
              png_bytep data,
              png_size_t length)
{
  std::ostream *os((std::ostream *) png_get_io_ptr(png_ptr));
  os->write((const char *) data, (size_t) length);
}

void
userFlushData(png_structp png_ptr)
{
}

PNGCanvas::PNGCanvas(const size_t width,
		       const size_t height,
		       const Properties& prop)
  : BitMapCanvas(width, height, prop, 72, 72)
{
}

bool 
PNGCanvas::close()
{
  mContents.clear();
  std::ostringstream os;

  typedef std::map<RGB, int> tColors;
  tColors colors;
  {
    int nColors(0);
    for (tMap::size_type i = 0; i < mMap.size(); ++i) {
      const tRow& row(mMap[i]);
      for (tRow::size_type j = 0; j < row.size(); ++j)
        if (colors.find(row[j]) == colors.end())
          colors.insert(std::make_pair(row[j], nColors++));
    }
  }

  if (colors.size() >= 256) {
    std::cerr << "Too many colors(" << colors.size() << ") found when trying to dump to PNG canvas" 
              << std::endl;
    throw "Too many colors found when trying to dump to PNG canvas";
  }

  const int bitDepth(colors.size() > 2 ? 8 : colors.size() == 2 ? 2 : 1);

  png_struct *png_ptr(png_create_write_struct(PNG_LIBPNG_VER_STRING, 0, 0, 0));

  if (!png_ptr) {
    std::cerr << "PNG Error creating write structure" << std::endl;
    return false;
  }

  const char *errmsg("setting jump");

  if (setjmp(png_jmpbuf(png_ptr))) {
    std::cerr << "PNG Error " << errmsg << std::endl;
    return false;
  }

  errmsg = "creating info structure";
  png_info *info_ptr(png_create_info_struct(png_ptr));

  if (!info_ptr) {
    std::cerr << "PNG Error creating info structure" << std::endl;
    return false;
  }

  errmsg = "during set_write_fn";
  png_set_write_fn(png_ptr, &os, userWriteData, userFlushData);

  errmsg = "while setting header";
  png_set_IHDR(png_ptr, info_ptr, (size_t) mWidth, (size_t) mHeight, bitDepth, 
               PNG_COLOR_TYPE_PALETTE, 
               PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_BASE, PNG_FILTER_TYPE_BASE);
  
  { // Take care of the palatte
    const int maximumColorMapSize(256);
    png_color rgb[maximumColorMapSize];
    
    memset(rgb, 0, sizeof(rgb));
    
    for (tColors::const_iterator it = colors.begin(); it != colors.end(); ++it) {
	const RGB& crgb(it->first);
	const int i(it->second);
	rgb[i].red   = crgb.red();
	rgb[i].green = crgb.green();
	rgb[i].blue  = crgb.blue();
    }
    
    errmsg = "while setting palette";
    png_set_PLTE(png_ptr, info_ptr, rgb, colors.size());
  }
  
  errmsg = "while setting compression level";
  png_set_compression_level(png_ptr, Z_BEST_COMPRESSION);
  
  errmsg = "while writing header";
  png_write_info(png_ptr, info_ptr);
  
  errmsg = "while writing image";
  
  for (tMap::size_type i = 0; i < mMap.size(); ++i) {
    const tRow& row(mMap[i]);
    unsigned char *ptr(new unsigned char[(size_t) mWidth]);
    memset(ptr, 0, sizeof(unsigned char) * (size_t) mWidth);
    for (tRow::size_type i = 0; i < row.size(); ++i)
	ptr[i] = colors.find(row[i])->second;
    png_write_row(png_ptr, ptr);
    delete ptr;
  }
  
  errmsg = "while writing end";
  png_write_end(png_ptr, 0);
 
  mContents = os.str();
 
  return true;
}

size_t
PNGCanvas::size()
{
  if (mContents.empty())
    close();

  return mContents.size();
}

const std::string&
PNGCanvas::str()
{
  if (mContents.empty())
    close();

  return mContents;
}

std::ostream&
operator << (std::ostream& os,
             PNGCanvas& c)
{
  if (c.mContents.empty())
    c.close();

  os << c.mContents;
  return os;
}
