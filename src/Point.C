#include <Point.H>
#include <iostream>

Point& Point::operator += (const Point& rhs)
{
  mX += rhs.mX;
  mY += rhs.mY;
  return *this;
}
 
Point Point::operator + (const Point& rhs) const
{
  Point pt(*this);
  pt += rhs;
  return pt;
}

Point& Point::operator -= (const Point& rhs)
{
  mX -= rhs.mX;
  mY -= rhs.mY;
  return *this;
}
 
Point Point::operator - (const Point& rhs) const
{
  Point pt(*this);
  pt -= rhs;
  return pt;
}

Point& Point::operator *= (const Point& rhs)
{
  mX *= rhs.mX;
  mY *= rhs.mY;
  return *this;
}
 
Point Point::operator * (const Point& rhs) const
{
  Point pt(*this);
  pt *= rhs;
  return pt;
}

Point& Point::operator /= (const Point& rhs)
{
  mX /= rhs.mX;
  mY /= rhs.mY;
  return *this;
}
 
Point Point::operator / (const Point& rhs) const
{
  Point pt(*this);
  pt /= rhs;
  return pt;
}

std::ostream&
operator << (std::ostream& os,
	       const Point& pt)
{
  os << "(" << pt.mX << "," << pt.mY << ")";
  return os;
}
