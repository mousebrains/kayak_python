#include <Axis.H>
#include <Canvas.H>
#include <Point.H>
#include <cmath>
#include <cstdio>
#include <iostream>

void
Axis::drawTicks(const tTicks& ticks,
		const Properties& prop)
{
  if (!ticks.empty()) {
    const bool qPush(mCanvas.maybePush(prop));
    const Properties p;

    for (tTicks::size_type size(ticks.size()), i(0); i < size; ++i) {
      mCanvas.line(Point(ticks[i].pos, ticks[i].start), Point(ticks[i].pos, ticks[i].stop), p);
    }

    {
      Properties lp(ticks[0].labelProp);
      bool allSame(true);

      for (tTicks::size_type size(ticks.size()), i(0); i < size; ++i) { 
        if (!ticks[i].label.empty() && (ticks[i].labelProp != lp)) {
          allSame = false;
          break;
        }
      }

      const bool flag(allSame && mCanvas.maybePush(lp.fontAnchor(mLabelAlignment)));

      for (tTicks::size_type size(ticks.size()), i(0); i < size; ++i) 
        if (!ticks[i].label.empty()) {
	  Properties pp(flag ? p : ticks[i].labelProp);
	  pp.translate(mLabelAdjustment + ticks[i].pos, ticks[i].offset);
          pp.fontAnchor(mLabelAlignment);
	  if (mLabelAngle)
	    pp.rotate(mLabelAngle);
	  mCanvas.text(ticks[i].label, Point(0, 0), pp);
	}

      mCanvas.maybePop(flag);
    }

    mCanvas.maybePop(qPush);
  }
}

void
Axis::drawGrid(const tGrid& grid,
               const Properties& prop)
{
  if (!grid.empty()) {
    const bool qPush(mCanvas.maybePush(prop));
    const Properties p;

    for (tGrid::size_type size(grid.size()), i(0); i < size; ++i)
      mCanvas.line(Point(grid[i], mGridMin), Point(grid[i], mGridMax), p);

    mCanvas.maybePop(qPush);
  }
}

void
Axis::axis(const double x1,
	   const double x2,
	   const Properties& axisProp,
	   const tTicks& ticks,
	   const tTicks& subTicks,
	   const tGrid& gridLines)
{
  Properties prop(axisProp);

  if (mxOrigin || myOrigin) {
    prop.translate(mxOrigin, myOrigin);
  }

  if (mAngle) {
    prop.rotate(mAngle);
  }
  

  const bool qPush(mCanvas.maybePush(prop));
  const Properties p;

  mCanvas.line(Point(x1, 0), Point(x2, 0), p);

  if (!mTitle.empty()) 
    mCanvas.text(mTitle, Point((x2 + x1) / 2, mTitleOffset), mTitleProp);

  drawGrid(gridLines, mGridProp);
  drawTicks(ticks, mTickProp);
  drawTicks(subTicks, mSubTickProp);

  mCanvas.maybePop(qPush);
}

void
Axis::operator () (const double ix1, // Canvas space start of axis
		   const double ix2, // Canvas space end of axis
		   const double x1, // Data space start of axis
		   const double x2, // Data space end of axis
		   const Properties& prop) // How to draw it
{
  if ((x1 == x2) || (ix1 == ix2))
    return;

  tTicks ticks, subTicks;
  tGrid gridLines;

  if (mNumberOfTicks > 0) {
    const double min(x1 < x2 ? x1 : x2); // Sorted start/end
    const double max(x1 < x2 ? x2 : x1); // Sorted start/end
    const double range(max - min);       // Length of axis
    const double slope((ix2 - ix1) / range); // pixels/user unit
    const double intercept(ix1 - slope * min); 

                      // Rounded step size in powers of 10 with at most mNumberOfTicks

    const double delta(findTickSpacing(range, mNumberOfTicks));
    const double firstTick(ceil(min / delta) * delta); // first tick rounded to delta
    const double lastTick(floor(max / delta) * delta); // last tick rounded to delta

    mAxisMin = min;
    mAxisMax  = max;

    for (double x(firstTick); x <= lastTick; x += delta) {
      const double xx(intercept + slope * x);

      if (mGrid) {
        gridLines.push_back(xx); 
      }

      std::string label;

      if (mqLabel) {
        char buffer[256];
        snprintf(buffer, sizeof(buffer), "%g", x);
        label = buffer;
      }

      ticks.push_back(Tick(xx, 0, mTickLength, label, mLabelOffset));
    }
    
    if (mNumberOfSubTicks > 0) {
      const double sDelta(findTickSpacing(delta, mNumberOfSubTicks));

      for (double sx(firstTick - sDelta); sx >= min; sx -= sDelta) { // Prior to first tick
	 subTicks.push_back(Tick(intercept + slope * sx, 
				 0, mSubTickLength, 
                                 std::string(), 0, Properties()));
      }

      for (double x(firstTick); x < max; x += delta) {
        const double xx(((x + delta) <= max) ? (x + delta - sDelta) : max);
        for (double sx(x + sDelta); sx <= xx; sx += sDelta) {
	    subTicks.push_back(Tick(intercept + slope * sx, 
				    0, mSubTickLength, 
                                    std::string(), 0, Properties()));
	}
      }
    }
  }

  axis(ix1, ix2, prop, ticks, subTicks, gridLines);
}

#define setParams(f, rt, nt, rs, ns) format = f; rTick = rt; nTicks = nt, sTick = rs, nSubTicks = ns

void
Axis::operator () (const double ix1, // Canvas space start of axis
		   const double ix2, // Canvas space end of axis
		   const time_t start, // Data space start of axis
		   const time_t end, // Data space end of axis
		   const Properties& prop) // How to draw it
{
  if ((start == end) || (ix1 == ix2))
    return;

  tTicks ticks, subTicks;
  tGrid gridLines;

  if (mNumberOfTicks > 0) {
    const double min(start < end ? start : end);
    const double max(start < end ? end : start);
    const double dt(max - min);
    const double slope((ix2 - ix1) / (end - start));
    const double intercept(ix1 - slope * start);
    const char *format(0); // How to format a label
    double rTick(1); // What to round ticks to
    int nTicks(mNumberOfTicks);
    double sTick(1); // What to round sub-ticks to 
    int nSubTicks(mNumberOfSubTicks);

    mAxisMin = min;
    mAxisMax  = max;

    // A cheaper search than a straight linear search
    if (dt <= 24 * 3600) { // Less than a day
      if (dt <= 20) { // Less than 20 seconds
	setParams("%S", 1, mNumberOfTicks, 1, 0);
      } else if (dt <= 60) { // Less than 1 minute
	setParams("%S", 10, 6, 1, 5);
      } else if (dt <= 120) { // Less than 2 minutes
	setParams("%M:%S", 30, 4, 5, 3);
      } else if (dt <= 600) { // Less than 10 minutes
	setParams("%M:%S", 60, 5, 10, 2);
      } else if (dt <= 3600) { // Less than 1 hour
	setParams("%M:%S", 300, 5, 60, 5);
      } else if (dt <= (2 * 3600)) { // Less than 2 hours
	setParams("%M:%S", 1200, 6, 120, 4);
      } else if (dt < (12 * 3600)) { // Less than 12 hours
	setParams("%M:%S", 3600, 6, 1200, 2);
      } else {
	setParams("%H:%M", 4 * 3600, 6, 1800, 4);
      }
    } else if (dt <= 14 * 24 * 3600) { // Less than a week
      if (dt <= (2 * 24 * 60 * 60)) { // Less than 48 hours
	setParams("%d %H", 6 * 3600, 7, 3600, 6);
      } else { // Less than 2 weeks
	setParams("%m/%d", 24 * 3600, 10, 4 * 3600, 6);
      }
    } else if (dt <= 2 * 366 * 24 * 3600) { // Less than 2 years
      if (dt <= (31 * 24 * 3600)) { // Less than 1 months
	setParams("%m/%d", 24 * 3600, 9, 24 * 3600, 3);
      } else if (dt <= (2 * 31 * 24 * 3600)) { // Less than 2 months
	setParams("%m/%d", 7 * 24 * 3600, 5, 24 * 3600, 7);
      } else if (dt <= 366 * 24 * 3600) { // Less than 1 year
	setParams("%m/%d", 30 * 24 * 3600, 5, 24 * 3600, 4);
      } else {
	setParams("%m/%d/%y", 120 * 24 * 3600, 5, 24 * 3600, 5);
      }
    } else {
      setParams("%m/%d/%y", 365 * 24 * 3600, 5, 30 * 24 * 3600, 12);
        nSubTicks = 12;
    }

    const double delta(findTimeTickSpacing(max - min, nTicks, rTick));
    double firstTick(ceil(min / delta) * delta);
    if (rTick >= 86400) { // More than a day, so localtime rounding
      const time_t t((time_t) firstTick);
      struct tm tm;
      localtime_r(&t, &tm);
      tm.tm_sec = 0;
      tm.tm_min = 0;
      tm.tm_hour = 0;
      firstTick = mktime(&tm);
      if (firstTick < min) {
        firstTick += rTick;
      }
    }

    for (double x = firstTick; x <= max; x += delta) {
      const double xx(intercept + slope * x);

      if (mGrid) {
        gridLines.push_back(xx); 
      }

      std::string label;
      if (mqLabel) {
        const time_t t((time_t) x);
        const struct tm *tm(localtime(&t));
        char buffer[256];
        strftime(buffer, sizeof(buffer), format, tm);
        label = buffer;
      }
      ticks.push_back(Tick(xx, 0, mTickLength, label, mLabelOffset));
    }

    const Properties p;

    if (delta > rTick) { // Put in intermediate ticks
      const double tickLength((mSubTickLength + mTickLength) / 2);
      for (double x = firstTick - delta; x < (max + delta); x += delta) {
	for (double dx = rTick; dx < delta; dx += rTick) 
	  if (((x + dx) >= min) && ((x + dx) <= max))
	    subTicks.push_back(Tick(intercept + slope * (x + dx), 0, 
				    tickLength, std::string(), 0, p));
      }
    }
   
    if (nSubTicks) {
      const double rSub(findTimeTickSpacing(rTick, nSubTicks, sTick));
// std::cerr << "rTick " << rTick << " n " << nSubTicks << " s " << sTick << " r " << rSub << " / " << rTick / rSub << std::endl;
      for (double x = firstTick - delta; x < (max + delta); x += rTick) {
	for (double dx = rSub; dx < rTick; dx += rSub) {
	  if (((x + dx) >= min) && ((x + dx) <= max)) {
	    subTicks.push_back(Tick(intercept + slope * (x + dx), 0, 
				    mSubTickLength, std::string(), 0, p));
          }
        }
      }
    }
  }

  axis(ix1, ix2, prop, ticks, subTicks, gridLines);
}

double
Axis::findTickSpacing(const double range,
                      const size_t maxNumberOfTicks)
{
  typedef std::vector<double> tNorm;
  static tNorm norm;

  if (norm.empty()) {
    norm.push_back(5);
    norm.push_back(4);
    norm.push_back(2);
  }

  const double delta(pow(10, ceil(log10(range / maxNumberOfTicks)))); 

  for (tNorm::size_type i(0), e(norm.size()); i < e; ++i) {
    const double d(delta / norm[i]);
    const size_t n(floor(range / d) + 1);
    if (n <= maxNumberOfTicks) {
      return d;
    }
  }

  return delta;
}

double
Axis::findTimeTickSpacing(const double range,
                          const size_t maxNumberOfTicks,
                          const double rangeNorm)
{
  const double nRange(range / rangeNorm);
  const double nDelta(ceil(nRange / maxNumberOfTicks));

// std::cerr << "ran " << range << " # " << maxNumberOfTicks << " norm " << rangeNorm << std::endl;
// std::cerr << " n " << nRange << " del " << minDelta << " " << maxDelta << " " << nDelta << std::endl;
  return nDelta * rangeNorm;
}
