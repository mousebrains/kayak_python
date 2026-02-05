#ifndef INC_BitMapCanvas_H_
#define INC_BitMapCanvas_H_

#include <Canvas.H>
#include <RGB.H>
#include <iosfwd>
#include <vector>

class BitMapCanvas : public Canvas {
protected:
  typedef std::vector<RGB> tRow;
  typedef std::vector<tRow> tMap;

  tMap mMap;
private:
  size_t mXdpi, mYdpi;

  bool point(const int x, const int y, const RGB& rgb);
public:
  BitMapCanvas(const size_t width, 
	       const size_t height, 
	       const Properties& prop,
	       const size_t xdpi = 100, 
	       const size_t ydpi = 100);

  virtual ~BitMapCanvas() {}

  virtual bool point(const Point& pt, const Properties& prop);
  virtual bool line(const Point& pt1, const Point& pt2, const Properties& prop);
  virtual bool text(const std::string& text, 
		    const Point& pt, 
		    const Properties& prop);

  friend std::ostream& operator << (std::ostream& os, const BitMapCanvas& bm);
};

#endif // INC_BitMapCanvas_H_
