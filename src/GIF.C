#include <GIF.H>
#include <gif_lib.h>
#include <png.h>
#include <map>
#include <iostream>
#include <cerrno>
#include <cstring>
#include <zlib.h>

namespace {
  ColorMapObject * colorMap(GifFileType *handle, 
                            SavedImage *image, 
                            const std::string& fn) 
  {
    if (image && image->ImageDesc.ColorMap)
      return image->ImageDesc.ColorMap;

    if (!handle) 
      return 0;

    if (handle->Image.ColorMap)
      return handle->Image.ColorMap;

    if (handle->SColorMap)
      return handle->SColorMap;

    std::cerr << "No color map found for " << fn << std::endl;
    return 0;
  }

  GifFileType *slurpImage(const std::string& fn, const int nImages = 1) {
    int errCode;
    GifFileType *ptr(DGifOpenFileName(fn.c_str(), &errCode));

    if (!ptr) {
      std::cerr << "Error opening " << fn << " for input, " 
                << GifErrorString(errCode)
                << std::endl;
      return 0;
    }
 
    if ((errCode = DGifSlurp(ptr)) == GIF_ERROR) {
      std::cerr << "Error slurping " << fn 
                << ", " << GifErrorString(errCode)
                << std::endl;
      DGifCloseFile(ptr);
      return 0;
    }

    if (nImages != ptr->ImageCount) {
      if (!ptr->ImageCount)
        std::cerr << "No image found in " << fn << std::endl;
      else
        std::cerr << "Found " << ptr->ImageCount << " images in " << fn
                  << " but I only wanted " << nImages << std::endl;
      DGifCloseFile(ptr);
      return 0;
    }
    return ptr;
  }

  int makePNGbitDepth(const int d) { return (d <= 1) ? 1 : (d <= 2) ? 2 : 8; }
}

GIF::GIF(const std::string& fn)
  : mFilename(fn),
    mHandle(slurpImage(fn))
{
  if (!mHandle) 
    return;
}

GIF::~GIF()
{
  cleanup();
}

void
GIF::cleanup()
{
  if (mHandle)
    DGifCloseFile(mHandle);
}

bool
GIF::comb(const std::string& fn)
{
  if (!*this)
    return false;

  GifFileType *in(slurpImage(fn));

  if (!in)
    return false;

  SavedImage& a = mHandle->SavedImages[0]; // Get saved image that has been slurped in
  SavedImage& b = in->SavedImages[0]; // Get saved image that has been slurped in


  if ((a.ImageDesc.Width != b.ImageDesc.Width) ||
      (a.ImageDesc.Height != b.ImageDesc.Height)) {
    std::cerr << mFilename 
              << "(" << a.ImageDesc.Width << "x" << a.ImageDesc.Height << ")"
              <<" has a different shape than " << fn
              << "(" << a.ImageDesc.Width << "x" << a.ImageDesc.Height << ")"
              << std::endl;
    DGifCloseFile(in);
    return false;
  }

  const ColorMapObject *colorA(colorMap(mHandle, &a, mFilename));
  const ColorMapObject *colorB(colorMap(in, &b, fn));

  if (!colorA || !colorB) {
    DGifCloseFile(in);
    return false;
  }

  GifPixelType colorTrans[256];
  ColorMapObject *colorUnion(GifUnionColorMap(colorA, colorB, colorTrans));

  if (!colorUnion) {
    std::cerr << "Unioned color map is too big(>256) for " 
              << mFilename << " and " << fn << std::endl;
    DGifCloseFile(in);
    return false;
  }

  if (a.ImageDesc.ColorMap)
    GifFreeMapObject(a.ImageDesc.ColorMap);
  a.ImageDesc.ColorMap = colorUnion;

  const int size(a.ImageDesc.Width * a.ImageDesc.Height);

  for (int i = 0; i < size; ++i) 
    if (b.RasterBits[i] != in->SBackGroundColor)
      a.RasterBits[i] = colorTrans[b.RasterBits[i]];

  return true;
}

bool
GIF::dump(const std::string& fn)
{
  if (!*this)
    return false;

  int errCode;
  GifFileType *out(EGifOpenFileName(fn.c_str(), false, &errCode));

  if (!out) {
    std::cerr << "Error opening " << fn << " for output, " 
              << GifErrorString(errCode)
              << std::endl;
    return false;
  }

  SavedImage *sm(mHandle->SavedImages);

  const ColorMapObject *cm(colorMap(mHandle, sm, mFilename));

  if (!cm) {
    EGifCloseFile(out);
    return false;
  }

  ColorMapObject *ncm(GifMakeMapObject(cm->ColorCount, cm->Colors));
  if (!ncm) {
    std::cerr << "Error making a map object for '" << fn << "'" << std::endl;
    EGifCloseFile(out);
    return false;
  }

  out->SWidth = mHandle->SWidth;
  out->SHeight = mHandle->SHeight;
  out->SBackGroundColor = mHandle->SBackGroundColor;
  out->SColorResolution = ncm->BitsPerPixel;
  out->SColorMap = ncm;
    
  for (int i = 0; i < mHandle->ImageCount; ++i)
    GifMakeSavedImage(out, &mHandle->SavedImages[i]);

  if ((errCode = EGifSpew(out)) == GIF_ERROR) {
    std::cerr << "Error making a saved image for '" << fn << "', "
              << GifErrorString(errCode) << std::endl;
    return false;
  }

  return true;
}

bool
GIF::dumpPNG(const std::string& fn)
{
  if (!*this)
    return false;

  SavedImage *sm(mHandle->SavedImages);
  const ColorMapObject *cm(colorMap(mHandle, sm, mFilename));

  if (!cm) 
    return false;

  const int width(mHandle->SWidth);
  const int height(mHandle->SHeight);
  const int bitDepth(makePNGbitDepth(cm->BitsPerPixel));

  png_struct *png_ptr(png_create_write_struct(PNG_LIBPNG_VER_STRING, 0, 0, 0));

  if (!png_ptr) {
    std::cerr << "PNG Error creating write structure" << std::endl;
    return false;
  }

  FILE *fp(0);
  const char *errmsg("creating info structure");

  if (setjmp(png_jmpbuf(png_ptr))) {
    std::cerr << "PNG Error " << errmsg << std::endl;
    if (fp)
      fclose(fp);
    return false;
  }

  png_info *info_ptr(png_create_info_struct(png_ptr));

  if (!info_ptr) {
    std::cerr << "PNG Error creating info structure" << std::endl;
    return false;
  }

  if (!(fp = fopen(fn.c_str(), "wb"))) {
    std::cerr << "Error opening " << fn << " for writing, " << strerror(errno) << std::endl;
    return false;
  }

  errmsg = "during init_io";
  png_init_io(png_ptr, fp);

  errmsg = "while setting header";
  png_set_IHDR(png_ptr, info_ptr, width, height, bitDepth, PNG_COLOR_TYPE_PALETTE, 
               PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_BASE, PNG_FILTER_TYPE_BASE);

  { // Take care of the palatte
    const int maximumColorMapSize(256);
    png_color rgb[maximumColorMapSize];

    memset(rgb, 0, sizeof(rgb));

    for (int i = 0; i < cm->ColorCount; ++i) {
      rgb[i].red = cm->Colors[i].Red;
      rgb[i].green = cm->Colors[i].Green;
      rgb[i].blue = cm->Colors[i].Blue;
    }

    errmsg = "while setting palette";
    png_set_PLTE(png_ptr, info_ptr, rgb, cm->ColorCount);
  }
 
  errmsg = "while setting compression level";
  png_set_compression_level(png_ptr, Z_BEST_COMPRESSION);

  errmsg = "while writing header";
  png_write_info(png_ptr, info_ptr);

  {
    unsigned char *ptr(sm->RasterBits); 
    errmsg = "while writing image";

    for (int i = 0; i < height; ++i) {
      png_write_row(png_ptr, ptr);
      ptr += width;
    }
  }

  errmsg = "while writing end";
  png_write_end(png_ptr, 0);

  fclose(fp);

  return true;
}

bool
GIF::isGray()  const
{
  SavedImage *sm(mHandle->SavedImages);
  const ColorMapObject *cm(colorMap(mHandle, sm, mFilename));

  if (!cm) 
    return false;

  const GifColorType *rgb(cm->Colors); 

  for (int i = 0; i < cm->ColorCount; ++i) 
    if (!isGray(rgb[i]))
      return false;
 
  return true; 
}

bool
GIF::isGray(const GifColorType& rgb) const
{
  return (rgb.Red == rgb.Green) && (rgb.Red == rgb.Blue);
}

#ifdef TPW
int
main (int argc,
      char **argv)
{
  if (argc < 3) {
    std::cerr << "Usage: " << argv[0] << " outputfn inputfiles..."
              << std::endl;
    return 1;
  }

  const char *fn(argv[1]);

  GIF gif(argv[2]);

  for (int i = 3; i < argc; ++i)  
    gif.comb(argv[i]);

  gif.dump(fn);
  gif.dumpPNG(fn + std::string(".png"));

  return 0;
}
#endif // TPW
