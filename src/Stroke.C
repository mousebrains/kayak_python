#include <Stroke.H>
#include <iostream>

std::ostream&
operator << (std::ostream& os,
	     const Stroke& s)
{
  os << s.stroke();
  return os;
}
