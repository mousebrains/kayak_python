#ifndef INC_RGB_H
#define INC_RGB_H

#include <iosfwd>

class Stroke;

class RGB {
public:
  typedef unsigned char Component;
private:
  int mRGB;

  int composite(const Component red, const Component green, const Component blue) const {
    return ((red << 16) & 0xff0000) | ((green << 8) & 0x00ff00) | (blue & 0x0000ff);
  }
public:
  RGB() : mRGB(0) {}

  RGB(const Component red, const Component green, const Component blue) 
    : mRGB(composite(red, green, blue))
  {}

  virtual ~RGB() {}

  bool operator == (const RGB& rhs) const {return mRGB == rhs.mRGB;}
  bool operator != (const RGB& rhs) const {return mRGB != rhs.mRGB;}
  bool operator < (const RGB& rhs) const {return mRGB < rhs.mRGB;}

  RGB& operator += (const RGB& f);
  RGB operator  +  (const RGB& f) const;

  RGB& operator -= (const RGB& f);
  RGB operator  -  (const RGB& f) const;

  RGB& operator *= (const double f);
  RGB operator  *  (const double f) const;

  RGB& operator /= (const double f);
  RGB operator  /  (const double f) const;

  Component red() const {return (mRGB >> 16) & 0xff;}
  Component green() const {return (mRGB >> 8) & 0xff;}
  Component blue() const {return mRGB & 0xff;}

  void red(const Component c)   {
    mRGB = (mRGB & 0x00ffff) | ((c << 16) & 0xff0000);
  }
  void green(const Component c) {
    mRGB = (mRGB & 0xff00ff) | ((c << 8) & 0x00ff00);
  }
  void blue(const Component c)  {
    mRGB = (mRGB & 0xffff00) | (c & 0x0000ff);
  }

  static const RGB& find(const Stroke& stroke);
  static const RGB& find(const std::string& color);

  friend std::ostream& operator << (std::ostream& os, const RGB& c);
};

#endif // INC_RGB_H
