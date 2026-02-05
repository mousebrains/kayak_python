#include <Properties.H>
#include <iostream>
#include <sstream>
#include <cmath>
#include <cstdlib>

Properties::Properties()
  : mStrokeWidth(strtod("NAN", 0)),
    mFontSize(mStrokeWidth),
    mFontRotation(NOTSET)
{
}

bool
Properties::operator == (const Properties& rhs) const
{
  return (mTransform == rhs.mTransform &&
          mStroke == rhs.mStroke &&
          mFill == rhs.mFill &&
          mStrokeWidth == rhs.mStrokeWidth &&
          mFontFamily == rhs.mFontFamily &&
          mFontSize == rhs.mFontSize &&
          mFontAnchor == rhs.mFontAnchor &&
          mBackground == rhs.mBackground
         );
}

Properties
Properties::operator | (const Properties& rhs) const
{
  Properties p(*this);
  p |= rhs;
  return p;
}
  
Properties&
Properties::operator |= (const Properties& rhs)
{
  mTransform *= rhs.mTransform;

  if (rhs.mStroke && (rhs.mStroke != mStroke))
    mStroke = rhs.mStroke;

  if (rhs.mFill && (rhs.mFill != mFill))
    mFill = rhs.mFill;

  if (!std::isnan(rhs.mStrokeWidth) && (rhs.mStrokeWidth != mStrokeWidth))
    mStrokeWidth = rhs.mStrokeWidth; 

  if (!rhs.mFontFamily.empty() && (rhs.mFontFamily != mFontFamily))
    mFontFamily = rhs.mFontFamily;

  if (!std::isnan(rhs.mFontSize) && (rhs.mFontSize != mFontSize))
    mFontSize = rhs.mFontSize; 

  if (!rhs.mFontAnchor.empty() && (rhs.mFontAnchor != mFontAnchor))
    mFontAnchor = rhs.mFontAnchor; 

  if (rhs.mBackground && (rhs.mBackground != mBackground))
    mBackground = rhs.mBackground;

  if (rhs.mFontRotation != NOTSET)
    mFontRotation = rhs.mFontRotation;

  return *this;
}

std::string
Properties::svgXML() const
{
  std::ostringstream os;

  std::string space;

  if (mTransform) {
    os << space << "transform='" << mTransform.actions() << "'";
    space = " ";
  }
 
  if (mStroke) {
    os << space << "stroke='" << mStroke << "'";
    space = " ";
  }
 
  if (mFill) {
    os << space << "fill='" << mFill << "'";
    space = " ";
  }
 
  if (!std::isnan(mStrokeWidth)) {
    os << space << "stroke-width='" << mStrokeWidth << "'";
    space = " ";
  }

  if (!mFontFamily.empty()) {
    os << space << "font-family='" << mFontFamily << "'";
    space = " ";
  }
 
  if (!std::isnan(mFontSize)) {
    os << space << "font-size='" << mFontSize << "'";
    space = " ";
  }
 
  if (!mFontAnchor.empty()) {
    os << space << "text-anchor='" << mFontAnchor << "'";
    space = " ";
  }
 
  return os.str();
}

std::string
Properties::svgXML(const Properties& p) const
{
  Properties a;

  if (mTransform && (p.mTransform != mTransform)) a.mTransform = mTransform;
  if (mStroke && (p.mStroke != mStroke)) a.mStroke = mStroke;
  if (mFill && (p.mFill != mFill)) a.mFill = mFill;
  if (!std::isnan(mStrokeWidth) && (p.mStrokeWidth != mStrokeWidth)) a.mStrokeWidth = mStrokeWidth;
  if (!mFontFamily.empty() && (p.mFontFamily != mFontFamily)) a.mFontFamily = mFontFamily;
  if (!std::isnan(mFontSize) && (p.mFontSize != mFontSize)) a.mFontSize = mFontSize;
  if (!mFontAnchor.empty() && (p.mFontAnchor != mFontAnchor)) a.mFontAnchor = mFontAnchor;

  return a.svgXML();
}

Properties
Properties::makeStroke(const std::string& color)
{
  Properties p;
  return p.stroke(color).fill(color);
}

std::ostream&
operator << (std::ostream& os,
	     const Properties& p)
{
  os << p.svgXML();
  return os;
}
