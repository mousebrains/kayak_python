#include <Transform.H>
#include <Point.H>
#include <iostream>
#include <cmath>
#include <cstdio>

Transform::Transform()
{
  t[0] = 1;
  t[1] = 0;
  t[2] = 0;
  t[3] = 0;
  t[4] = 1;
  t[5] = 0;
}

Transform::operator bool () const
{
  return t[0] != 1 || t[1] != 0 || t[2] != 0 || t[3] != 0 || t[4] != 1 || t[5] != 0;
}

Transform::Transform(const double a,
		       const double b,
		       const double c,
		       const double d,
		       const double e,
		       const double f)
{
  t[0] = a;
  t[1] = b;
  t[2] = c;
  t[3] = d;
  t[4] = e;
  t[5] = f;
}

Transform& Transform::operator *= (const Transform& b)
{
  Transform c(*this * b);
  *this = c;
  return *this;
}

Transform Transform::operator * (const Transform& b) const 
{	
  //    std::cout << "* this " << *this << std::endl;
  //    std::cout << "b " << b << std::endl;
  Transform c(*this);

  c[0] = t[0] * b[0] + t[1] * b[3];
  c[1] = t[0] * b[1] + t[1] * b[4];
  c[2] = t[0] * b[2] + t[1] * b[5] + t[2];
  c[3] = t[3] * b[0] + t[4] * b[3];
  c[4] = t[3] * b[1] + t[4] * b[4];
  c[5] = t[3] * b[2] + t[4] * b[5] + t[5];

  //    std::cout << "c " << c << std::endl;
  return c;
}

Point Transform::operator * (const Point& a) const 
{
  Point b(t[0] * a.x() + t[1] * a.y() + t[2],
	    t[3] * a.x() + t[4] * a.y() + t[5]);
  return b;
}

void 
Transform::translate(const double x, 
                     const double y) 
{
  *this *= Transform(1, 0, x, 0, 1, y);

  char buffer[256];
  snprintf(buffer, sizeof(buffer), "%stranslate(%g,%g)", (mActions.empty() ? "" : " "), x, y);
  mActions += buffer;
}

void Transform::scale(const double sx, 
                      const double sy) 
{
  *this *= Transform(sx, 0, 0, 0, sy, 0);

  char buffer[256];
  snprintf(buffer, sizeof(buffer), "%sscale(%g,%g)", (mActions.empty() ? "" : " "), sx, sy);
  mActions += buffer;
}

void Transform::scale(const double s)
{
  *this *= Transform(s, 0, 0, 0, s, 0);

  char buffer[256];
  snprintf(buffer, sizeof(buffer), "%sscale(%g)", (mActions.empty() ? "" : " "), s);
  mActions += buffer;
}

void 
Transform::rotate(const double theta) 
{
  const double s(sin(theta * M_PI / 180));
  const double c(cos(theta * M_PI / 180));
  *this *= Transform(c, -s, 0, s, c, 0);

  char buffer[256];
  snprintf(buffer, sizeof(buffer), "%srotate(%g)", (mActions.empty() ? "" : " "), theta);
  mActions += buffer;
}

bool
Transform::operator == (const Transform& rhs) const
{
  return (t[0] == rhs.t[0] &&
          t[1] == rhs.t[1] &&
          t[2] == rhs.t[2] &&
          t[3] == rhs.t[3] &&
          t[4] == rhs.t[4] &&
          t[5] == rhs.t[5]);
}

std::ostream& operator << (std::ostream& os, const Transform& t) {
  os << "{" 
     << t[0] << "," << t[1] << "," << t[2] << ","
     << t[3] << "," << t[4] << "," << t[5]
     << "} (" << t.mActions << ")";
  return os;
}
