#include <Points.H>
#include <iostream>
#include <string>

std::ostream& 
operator << (std::ostream& os,
	       const Points& pts)
{
  for (Points::const_iterator it = pts.begin(); it != pts.end(); ++it) {
    os << (it == pts.begin() ? "" : " ") << *it;
  }

  return os;
}
