#ifndef INC_Points_H_
#define INC_Points_H_

#include <Point.H>
#include <vector>
#include <iosfwd>

class Points {
public:
  typedef std::vector<Point> tPoints;
  typedef tPoints::const_iterator const_iterator;
  typedef tPoints::size_type size_type;
private:
  tPoints mPoints;
public:
  Points() {}
  Points(tPoints::size_type len) : mPoints(len) {}
  
  void push_back(const Point& pt) {mPoints.push_back(pt);}
  
  bool empty() const {return mPoints.empty();}
  size_type size() const {return mPoints.size();}
  const_iterator begin() const {return mPoints.begin();}
  const_iterator end() const {return mPoints.end();}

  const Point& operator [] (const size_type i) const {return mPoints[i];}
  Point& operator [] (const size_type i) {return mPoints[i];}
};

std::ostream& operator << (std::ostream& os, const Points& pts);

#endif // INC_Points_H_
