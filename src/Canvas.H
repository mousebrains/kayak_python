#ifndef INC_Graph_Canvas_H
#define INC_Graph_Canvas_H

#include <Properties.H>
#include <string>
#include <vector>
#include <iosfwd>

class Point;
class Points;
class Stroke;

class Canvas {
protected:
  Properties mProperties;
  typedef std::vector<Properties> tStack;
  tStack mStack;

  double mWidth;
  double mHeight;
private:
  Canvas();
  Canvas(Canvas& c);
public:
  Canvas(const Properties& prop, 
         const double width, 
         const double height);

  virtual ~Canvas() {}

  virtual bool line(const Point &pt1, const Point &pt2, const Properties& prop = Properties()) = 0;
  virtual bool text(const std::string& string, const Point& pt, 
                    const Properties& prop = Properties()) = 0;

  virtual bool close() = 0;

  virtual bool point(const Point& pt, const Properties& prop = Properties());
  virtual bool rectangle(const Point& pt, const Point& size, const Properties& prop = Properties());
  virtual bool polyline(const Points& pts, const Properties& prop = Properties());

  const Properties& properties() const {return mProperties;}

  virtual bool maybePush(const Properties& prop);
  virtual void push(const Properties& prop);

  virtual bool maybePop(const bool qPop) {return qPop ? pop() : false;}
  virtual bool pop();

  double width() const {return mWidth;}
  double height() const {return mHeight;}

  virtual size_t size() = 0;

  virtual const std::string& str() = 0;
  virtual const char *mimetype() const = 0;

  friend std::ostream& operator << (std::ostream& os, const Canvas& c);
};

#endif // INC_Graph_Canvas_H
