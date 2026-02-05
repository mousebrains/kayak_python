#ifndef INC_SVGCanvas_H_
#define INC_SVGCanvas_H_

#include <Canvas.H>
#include <sstream>
#include <string>

class SVGCanvas : public Canvas {
private:
  mutable std::ostringstream mOS;
  mutable std::string mContents;
public:
  SVGCanvas(const std::string& description, 
            const std::string& width, 
            const std::string& height, 
            const Properties& prop);

  virtual ~SVGCanvas();

  virtual bool maybePush(const Properties& prop);
  virtual void push(const Properties& prop);
  virtual bool pop();

  virtual bool rectangle(const Point& ptll, const Point& ptur, 
                         const Properties& prop = Properties());
  virtual bool polyline(const Points& pts, const Properties& prop = Properties());
  virtual bool line(const Point& pt1, const Point& pt2, 
                    const Properties& prop = Properties());
  virtual bool text(const std::string& text, 
		    const Point& pt, 
		    const Properties& prop = Properties());

  virtual bool close();
  virtual size_t size();
  virtual const std::string& str();
  virtual const char *mimetype() const {return "image/svg+xml";}

  friend std::ostream& operator << (std::ostream& os, SVGCanvas& bm);
};

#endif // INC_SVGCanvas_H_
