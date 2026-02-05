#ifndef INC_Stroke_H
#define INC_Stroke_H

#include <string>
#include <iosfwd>

class Stroke {
private:
  std::string mStroke;
public:
  Stroke() {}
  Stroke(const std::string& stroke) : mStroke(stroke) {}
  virtual ~Stroke() {}

  operator bool () const {return !mStroke.empty();}

  bool operator < (const Stroke& rhs) const {return mStroke < rhs.mStroke;}

  bool operator != (const Stroke& rhs) const {return mStroke != rhs.mStroke;}
  bool operator == (const Stroke& rhs) const {return mStroke == rhs.mStroke;}

  const std::string& stroke() const {return mStroke;}
};

std::ostream& operator << (std::ostream& os, const Stroke& c);

#endif // INC_Stroke_H
