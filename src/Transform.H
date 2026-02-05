#ifndef INC_Transform_H_
#define INC_Transform_H_

#include <iosfwd>
#include <string>

class Point;

class Transform {
private:
  std::string mActions;

  double t[6];
  
  double operator [] (const size_t i) const {return t[i];}
  double& operator [] (const size_t i) {return t[i];}
public:
  Transform();
  Transform(double a, double b, double c, double d, double e, double f);
 
  operator bool () const;
 
  Transform& operator *= (const Transform& b);
  Transform operator * (const Transform& b) const;
  Point operator * (const Point& a) const;

  void translate(const double x, const double y);
  void scale(const double sx, const double sy);
  void scale(const double s);
  void rotate(const double theta);
 
  bool operator == (const Transform& rhs) const;
  bool operator != (const Transform& rhs) const {return !(*this == rhs);};

  const std::string& actions() const {return mActions;}
 
  friend std::ostream& operator << (std::ostream& os, const Transform& t);
};

#endif // INC_Transform_H_
