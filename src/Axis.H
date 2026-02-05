#ifndef INC_Axis_H_
#define INC_Axis_H_

#include <Properties.H>
#include <vector>
#include <string>

class Canvas;

class Axis {
private:
  Canvas& mCanvas;

  double mAngle;
  size_t mNumberOfTicks;
  size_t mNumberOfSubTicks;
  double mTickLength;
  double mSubTickLength;
  double mLabelOffset;
  double mLabelAngle;
  std::string mLabelAlignment;
  double mLabelAdjustment;

  Properties mTickProp;
  Properties mSubTickProp;

  std::string mTitle;
  double mTitleOffset;
  Properties mTitleProp;

  bool mGrid;
  Properties mGridProp;
  double mGridMin;
  double mGridMax;

  double mAxisMin;
  double mAxisMax;

  double mxOrigin;
  double myOrigin;

  bool mqLabel;

  static double findTickSpacing(const double range, const size_t maxNumberOfTicks);
  static double findTimeTickSpacing(const double range, const size_t maxNumberOfTicks, const double rangeNorm);
public:
  Axis(Canvas& canvas) 
    : mCanvas(canvas),
      mAngle(0),
      mNumberOfTicks(5),
      mNumberOfSubTicks(1),
      mTickLength(-10),
      mSubTickLength(mTickLength / 2),
      mLabelOffset(mTickLength * 2),
      mLabelAngle(0),
      mLabelAlignment("middle"),
      mLabelAdjustment(0),
      mTitleOffset(mLabelOffset * 2),
      mGrid(false),
      mAxisMin(0),
      mAxisMax(0),
      mxOrigin(0),
      myOrigin(0),
      mqLabel(true)
  {}

  virtual ~Axis() {}

  class Tick {
  public:
    double pos;
    double start;
    double stop;
    std::string label;
    double offset;
    Properties labelProp;

    Tick(const double p, const double s, const double e, const std::string l,
	 const double o, const Properties& lp = Properties())
      : pos(p), start(s), stop(e), label(l), offset(o), labelProp(lp) {}
  };

  typedef std::vector<Tick> tTicks;
  typedef std::vector<double> tGrid;

  void drawTicks(const tTicks& ticks, 
		 const Properties& prop);

  void drawGrid(const tGrid& grid, const Properties& prop);

  void axis(const double ix1, // Canvas space start of axis
	    const double ix2, // Canvas space end of axis
	    const Properties& axisProperties, // Properties for drawing the axis
	    const tTicks& ticks, // tick locations and labels
	    const tTicks& subTicks, // sub tick locations and labels
	    const tGrid& gridLines); // Grid lines

  void operator () (const double ix1, // Canvas space start of axis
		    const double ix2, // Canvas space end of axis
		    const double min, // Data space start of axis
		    const double max, // Data space end of axis
		    const Properties& prop); // How to draw everything

  void operator () (const double ix1, // Canvas space start of axis
		    const double ix2, // Canvas space end of axis
		    const time_t start, // Data space start of axis
		    const time_t end, // Data space end of axis
		    const Properties& prop); // How to draw everything

  Axis& angle(const double angle) {mAngle = angle; return *this;}
  double angle() const {return mAngle;}

  Axis& nTicks(const size_t nTicks) {mNumberOfTicks = nTicks; return *this;}
  size_t nTicks() const {return mNumberOfTicks;}

  Axis& nSubTicks(const size_t nSubTicks) {mNumberOfSubTicks = nSubTicks; return *this;}
  size_t nSubTicks() const {return mNumberOfSubTicks;}

  Axis& tickLength(const double tickLength) {mTickLength = tickLength; return *this;}
  double tickLength() const {return mTickLength;}

  Axis& subTickLength(const double subTickLength) {mSubTickLength = subTickLength; return *this;}
  double subTickLength() const {return mSubTickLength;}

  Axis& labelOffset(const double labelOffset) {mLabelOffset = labelOffset; return *this;}
  double labelOffset() const {return mLabelOffset;}

  Axis& labelAngle(const double labelAngle) {mLabelAngle = labelAngle; return *this;}
  double labelAngle() const {return mLabelAngle;}

  Axis& labelAdjustment(const double labelAdjustment) {mLabelAdjustment = labelAdjustment; return *this;}
  double labelAdjustment() const {return mLabelAdjustment;}

  Axis& labelAlignment(const std::string& labelAlignment) {
    mLabelAlignment = labelAlignment; 
    return *this;
  }
  const std::string& labelAlignment() const {return mLabelAlignment;}

  Axis& tickProp(const Properties& p) {mTickProp = p; return *this;}
  const Properties& tickProp() const {return mTickProp;}

  Axis& subTickProp(const Properties& p) {mSubTickProp = p; return *this;}
  const Properties& subTickProp() const {return mSubTickProp;}

  Axis& title(const std::string& title) {mTitle = title; return *this;}
  const std::string& title() const {return mTitle;}

  Axis& titleOffset(const double offset) {mTitleOffset = offset; return *this;}
  double titleOffset() const {return mTitleOffset;}

  Axis& titleProp(const Properties& p) {mTitleProp = p; return *this;}
  const Properties& titleProp() const {return mTitleProp;}

  Axis& grid(const bool g) {mGrid = g; return *this;}
  bool grid() const {return mGrid;}

  Axis& gridProp(const Properties& p) {mGridProp = p; return *this;}
  const Properties& gridProp() const {return mGridProp;}

  Axis& gridLimits(const double min, const double max) {
    mGridMin = min; 
    mGridMax = max; 
    return *this;
  }

  double gridMin() const {return mGridMin;}
  double gridMax() const {return mGridMax;}

  double axisMin() const {return mAxisMin;}
  double axisMax() const {return mAxisMax;}

  Axis& xOrigin(const double x) {mxOrigin = x; return *this;}
  Axis& yOrigin(const double y) {myOrigin = y; return *this;}
  double xOrigin() const {return mxOrigin;}
  double yOrigin() const {return myOrigin;}

  Axis& qLabel(const bool q) {mqLabel = q; return *this;}
  bool qLabel() const {return mqLabel;}
};

#endif // INC_Axis_H_
