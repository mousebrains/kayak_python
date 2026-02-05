#ifndef INC_PNGCanvas_H_
#define INC_PNGCanvas_H_

#include <BitMapCanvas.H>

class PNGCanvas : public BitMapCanvas {
  mutable std::string mContents;
public:
  PNGCanvas(const size_t width, 
	    const size_t height, 
	    const Properties& properties);

  virtual ~PNGCanvas() {}

  virtual bool close();
  virtual size_t size();
  virtual const std::string& str();
  virtual const char *mimetype() const {return "image/png";}

  friend std::ostream& operator << (std::ostream& os, PNGCanvas& canvas);
};

#endif // INC_PNGCanvas_H_
