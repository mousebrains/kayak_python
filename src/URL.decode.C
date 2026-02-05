#include <URL.H>

static int
dehex(const int c)
{
  if ((c >= '0') && (c <= '9'))
    return (c - '0') & 0x0f;
  if ((c >= 'a') && (c <= 'f'))
    return ((c - 'a') + 10) & 0x0f;
  if ((c >= 'A') && (c <= 'F'))
    return ((c - 'A') + 10) & 0x0f;
  return 0;
}

std::string
URL::decode(const std::string& src)
{
  std::string dest;
  std::string::size_type len(src.size());

  int b;
  int sumb(0);
  int more = -1;
  for (std::string::size_type i = 0; i < len; ++i) {
    int c(src[i]);
    if (c == '%') {
      const int hb(dehex(src.at(++i)));
      const int lb(dehex(src.at(++i)));
      b = (hb << 4) | lb;
    } else if (c == '+')
      b = ' ';
    else
      b = c;

    if ((b & 0xc0) == 0x80) {          // 10xxxxxx (continuation byte)
      sumb = (sumb << 6) | (b & 0x3f); // Add 6 bits to sumb
      if (--more == 0)
        dest += sumb;
    } else if ((b & 0x80) == 0x00) {   // 0xxxxxx (yields 7 bits)
      dest += b;
    } else if ((b & 0xf0) == 0xc0) {   // 110xxxxx (yields 5 bits)
      sumb = b & 0x1f;
      more = 1;
    } else if ((b & 0xf0) == 0xe0) {   // 1110xxxx (yields 4 bits)
      sumb = b & 0x0f;
      more = 2;
    } else if ((b & 0xf8) == 0xf0) {   // 11110xxx (yields 3 bits)
      sumb = b & 0x07;
      more = 3;
    } else if ((b & 0xfc) == 0xf8) {   // 111110xx (yields 2 bits)
      sumb = b & 0x03;
      more = 4;
    } else /* if ((b & 0xfe) == 0xfc) */ {   // 1111110x (yields 1 bits)
      sumb = b & 0x01;
      more = 5;
    }
  }
  return dest;
}
