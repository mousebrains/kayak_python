#ifndef INC_Property_H_
#define INC_Property_H_

#include <Transform.H>
#include <Stroke.H>

class Properties {
public:
  enum FONTROTATION {
    STEPX,
    STEPY,
    STEPmX,
    STEPmY,
    NOTSET};

private:
  Transform mTransform;
  Stroke mStroke;
  Stroke mFill;
  Stroke mBackground;
  double mStrokeWidth;
  std::string mFontFamily;
  double mFontSize;
  std::string mFontAnchor; 

  FONTROTATION mFontRotation;
    
public:
  Properties();

  Properties& translate(const double x, const double y) {mTransform.translate(x, y); return *this;}
  Properties& scale(const double sx, const double sy) {mTransform.scale(sx, sy); return *this;}
  Properties& scale(const double s) {mTransform.scale(s); return *this;}
  Properties& rotate(const double theta) {mTransform.rotate(theta); return *this;}
  const Transform& transform() const {return mTransform;}

  Properties& stroke(const Stroke& stroke) {mStroke = stroke; return *this;}
  Properties& stroke(const std::string& color) {mStroke = Stroke(color); return *this;}
  const Stroke& stroke() const {return mStroke;}

  Properties& fill(const Stroke& fill) {mFill = fill; return *this;}
  Properties& fill(const std::string& color) {mFill = Stroke(color); return *this;}
  const Stroke& fill() const {return mFill;}

  Properties& background(const Stroke& background) {mBackground = background; return *this;}
  Properties& background(const std::string& color) {mBackground = Stroke(color); return *this;}
  const Stroke& background() const {return mBackground;}

  Properties& strokeWidth(const double width) {mStrokeWidth = width; return *this;}
  double strokeWidth() const {return mStrokeWidth;}

  Properties& fontFamily(const std::string& font) {mFontFamily = font; return *this;}
  const std::string& fontFamily() const {return mFontFamily;}

  Properties& fontSize(const double size) {mFontSize = size; return *this;}
  double fontSize() const {return mFontSize;}

  Properties& fontAnchor(const std::string& anchor) {mFontAnchor = anchor; return *this;}
  const std::string& fontAnchor() const {return mFontAnchor;}

  Properties& fontRotation(const FONTROTATION fr) {mFontRotation = fr; return *this;}
  FONTROTATION fontRotation() const {return mFontRotation;}

  bool operator == (const Properties& rhs) const;
  bool operator != (const Properties& rhs) const {return !(*this == rhs);}

  Properties operator | (const Properties& rhs) const;
  Properties& operator |= (const Properties& rhs);

  std::string svgXML() const;
  std::string svgXML(const Properties& p) const;

  static Properties makeStroke(const std::string& color);
};


std::ostream& operator << (std::ostream& os, const Properties& p);

#endif // INC_Property_H_
