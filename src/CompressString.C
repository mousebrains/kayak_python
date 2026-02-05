#include <CompressString.H>
#include <string>
#include <iostream>
#include <sstream>
#include <ctime>
#include <cstring>
#include <zlib.h>
// #include <z-4mk.h>

#ifndef OS_CODE // For gzip
#  define OS_CODE  0x03  /* assume Unix */
#endif

namespace {
  const unsigned char gz_magic[2] = {0x1f, 0x8b}; /* gzip magic header */
  int defaultCompressionLevel(Z_BEST_SPEED);

  bool
  zlibCheck(const int code,
            const std::string& msg)
  {
    if (code == Z_OK)
      return true;

    std::string err(msg + " ERROR(zlib): ");
    switch (code) {
      case Z_BUF_ERROR: err += "Not enough output buffer memory"; break;
      case Z_MEM_ERROR: err += "Not enough memory"; break;
      case Z_STREAM_END: err += "Stream end"; break;
      case Z_VERSION_ERROR: err += "Incompatabile ZLIB Versions"; break;
      default:
        std::cerr << "Unrecognized zlib code, " << code
                  << ", " << msg
                  << std::endl;
        return false;
    }
    std::cerr << err << std::endl;
    return false;
  }

  void
  putInt(std::ostream& os,
         int value)
  {
    for (int i = 0; i < 4; ++i) {
      const unsigned char ch(value & 0xff);
      value >>= 8;
      os.put(ch);
    }
  }

  size_t
  compressIt(std::ostream& os,
             Bytef *source,
             const uInt sourceLen)
  {
    uInt destLen(static_cast<uInt>(static_cast<double>(sourceLen) * 1.001 + 12));
    Bytef *dest(new Bytef[destLen]);
  
    z_stream c_stream; /* compression stream */

    c_stream.zalloc = 0;
    c_stream.zfree = 0;
    c_stream.opaque = 0;

    if (!zlibCheck(deflateInit(&c_stream, defaultCompressionLevel), "defaltInit"))
      return 0;

    c_stream.next_in  = source;
    c_stream.avail_in = sourceLen;

    c_stream.next_out = dest;
    c_stream.avail_out = destLen;

    const int code(deflate(&c_stream, Z_FINISH));
    if (code != Z_STREAM_END) {
      std::cerr << "defalt return code Should have been Z_STREAM_END, but was "
                << code << std::endl;
      zlibCheck(code, "defalte");
      return 0;
    }

    if (!zlibCheck(deflateEnd(&c_stream), "deflateEnd"))
      return 0;

    os.write((const char *) gz_magic, 
             static_cast<int>(sizeof(gz_magic)));
    os.put(Z_DEFLATED);
    os.put('\0'); // flags
    putInt(os, time(0));
    os.put('\4'); // extra flags
    os.put(OS_CODE);
    os.write((const char *) (dest + 2), c_stream.total_out - 6);
    putInt(os, crc32(crc32(0, 0, 0), source, sourceLen));
    putInt(os, c_stream.total_in);

    return (c_stream.total_out - 6 + 18);
  }
}

size_t
Compress::stream(std::ostream& os,
                 const char *source)
{
  return compressIt(os, (unsigned char *) source, strlen(source));
}

size_t
Compress::stream(std::ostream& os,
                 const char *source,
                 const size_t length)
{
  return compressIt(os, (unsigned char *) source, length);
}

size_t
Compress::stream(std::ostream& os,
                 const std::string& source)
{
  return compressIt(os, (unsigned char *) source.c_str(), source.size());
}

std::string
Compress::string(const char *source)
{
  return Compress::string(source, strlen(source));
}

std::string
Compress::string(const char *source,
                 const size_t length)
{
  std::ostringstream os;
  Compress::stream(os, source, length);
  return os.str();
}

std::string
Compress::string(const std::string& source)
{
  std::ostringstream os;
  Compress::stream(os, source);
  return os.str();
}

void Compress::bestSpeed() { defaultCompressionLevel = Z_BEST_SPEED; }
void Compress::bestCompression() { defaultCompressionLevel = Z_BEST_COMPRESSION; }
void Compress::level(int l) { defaultCompressionLevel = l; }
