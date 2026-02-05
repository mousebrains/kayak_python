#ifndef INC_Point_H
#define INC_Point_H

#include <iosfwd>

class Point {
private:
  double mX, mY;
public:
  Point() {}
  Point(const double x, const double y) : mX(x), mY(y) {}
  virtual ~Point() {}

  void x(const double x) {mX = x;}
  void y(const double y) {mY = y;}

  double x() const {return mX;}
  double y() const {return mY;}

  double& x() {return mX;}
  double& y() {return mY;}

  size_t ix() const {return (size_t) mX;}
  size_t iy() const {return (size_t) mY;}

  Point& operator += (const Point& rhs);
  Point operator + (const Point& rhs) const;

  Point& operator -= (const Point& rhs);
  Point operator - (const Point& rhs) const;

  Point& operator *= (const Point& rhs);
  Point operator * (const Point& rhs) const;

  Point& operator /= (const Point& rhs);
  Point operator / (const Point& rhs) const;

  friend std::ostream& operator << (std::ostream& os, const Point& c);
};

#endif // INC_Point_H
