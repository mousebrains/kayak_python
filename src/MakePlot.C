#include <MakePlot.H>
#include <Axis.H>
#include <Stroke.H>
#include <Points.H>
#include <Convert.H> // TPW
#include <iostream>
#include <fstream>
#include <cmath>

bool
MakePlot(Canvas& canvas,
         const Points& points,
         const std::string& title,
         const std::string& ylabel)
{
  if (points.empty())
    return false;

  time_t start((time_t) points[0].x()), end(start);
  double min(points[0].y()), max(min);

  for (Points::size_type i = 1; i < points.size(); ++i) {
    const time_t t((time_t) points[i].x());
    const double y(points[i].y());

    if (t < start) start = t;
    if (t > end) end = t;

    if (y < min) min = y;
    if (y > max) max = y;
  }

  // std::cerr << "t " << Convert::toString(start) << " " << Convert::toString(end) << std::endl;
  // std::cerr << "y " << min << " " << max << std::endl;

  if (start == end)
    return false;

  if (min == max) {
    max += 1;
    min -= 1;
  }

  { // Rationalize min/max
    double aMin(fabs(min) < fabs(max) ?  fabs(min) : fabs(max));
    if (aMin == 0) {
      aMin = fabs(min) < fabs(max) ?  fabs(max) : fabs(min);
      if (aMin == 0) {
        aMin = 1;
      }
    }
   
    const double rounding(pow(10, floor(log10(aMin) - 0.25)));
    min = floor(min / rounding) * rounding;
    max = ceil(max / rounding) * rounding;
    // std::cerr << "y' " << min << " " << max << " rnd " << rounding << " aMin " << aMin << std::endl;
  }

  const double sx1(0), sx2(canvas.width() * 0.8), ax2(canvas.width() * 0.85);
  const double sy1(0), sy2(canvas.height() * 0.8), ay2(canvas.height() * 0.85);

// std::cerr << "sx1 " << sx1 << " sx2 " << sx2 << " ax2 " << ax2 << std::endl;
// std::cerr << "sy1 " << sy1 << " sy2 " << sy2 << " ay2 " << ay2 << std::endl;

  { // set up initial coordinate system
    Properties p;
    p.translate(canvas.width() - ax2, ay2);
    p.scale(1, -1);

    canvas.push(p);
  }

  { // draw a rectangle around the plot
    Properties pp;
    pp.fill("none");
    canvas.rectangle(Point(0,0), Point(sx2, sy2), pp);
  }

  { // Put in the title
    canvas.text(title, Point((sx1 + sx2) / 2, sy2 + (ay2 - sy2) * 0.1), Properties());
  }

  const Properties gridProp(Properties::makeStroke("lightskyblue"));

  const double xTickLength(10);
  const double nxTicks(3);
  const double nxSubTicks(2);

  { // X axis bottom
    const double labelOffset(-20);
    const double titleOffset(-40);

    const time_t now(time(0));
    char buffer[256];
    strftime(buffer, sizeof(buffer), "Generated %m/%d/%Y @ %H:%M:%S %Z", localtime(&now));

    Axis axis(canvas);
    axis.tickLength(xTickLength).nTicks(nxTicks).nSubTicks(nxSubTicks);
    axis.subTickLength(axis.tickLength() / 2);
    axis.labelOffset(labelOffset);
    axis.title(buffer).titleOffset(titleOffset);
    axis.grid(true).gridLimits(sy1, sy2).gridProp(gridProp);

    axis(sx1, sx2, start, end, Properties::makeStroke("black"));
  } // X axis bottom
  { // X axis top
    Axis axis(canvas);
    axis.yOrigin(sy2).qLabel(false);
    axis.tickLength(-xTickLength).nTicks(nxTicks).nSubTicks(nxSubTicks);
    axis.subTickLength(axis.tickLength() / 2);

    axis(sx1, sx2, start, end, Properties());
  } // X axis top

  double yMin(min), yMax(max);

  const double yTickLength(-fabs(xTickLength));
  const double nyTicks(7); // Maximum number of ticks
  const double nySubTicks(9); // Maximum number of subticks

  { // Y axis left hand side
    const double labelOffset(5);
    const double titleOffset(60);

    Axis axis(canvas);
    axis.angle(90).tickLength(yTickLength).nTicks(nyTicks).nSubTicks(nySubTicks);
    axis.labelAngle(-90).labelAlignment("end").labelAdjustment(-5);
    axis.subTickLength(axis.tickLength() / 2);
    // axis.labelOffset(axis.tickLength() * 2);
    axis.labelOffset(labelOffset);
    // axis.title(ylabel).titleOffset(axis.labelOffset() * 3);
    axis.title(ylabel).titleOffset(titleOffset);
    axis.grid(true).gridLimits(sx1, -sx2).gridProp(gridProp);

    Properties prop(Properties::makeStroke("black"));
    axis.titleProp(prop.fontRotation(Properties::STEPmY));

    axis(sy1, sy2, min, max, Properties());

    yMin = axis.axisMin();
    yMax = axis.axisMax();
  } // Y axis left hand side
  { // Y axis right hand side
    Axis axis(canvas);
    axis.xOrigin(sx2).qLabel(false);
    axis.angle(90).tickLength(-yTickLength).nTicks(nyTicks).nSubTicks(nySubTicks);
    axis.subTickLength(axis.tickLength() / 2);

    axis(sy1, sy2, min, max, Properties());
  } // Y axis right hand side

  { // plot data
    Properties data(Properties::makeStroke("green"));
    data.fill("none");

    const double xSlope((sx2 - sx1) / (end - start));
    const double xInter(sx1 - xSlope * start);
    const double ySlope((sy2 - sy1) / (yMax - yMin));
    const double yInter(sy1 - ySlope * yMin);

    Points pts;

    for (Points::size_type i = 0; i < points.size(); ++i) {
      const Point pt(xInter + xSlope * points[i].x(), yInter + ySlope * points[i].y());
      pts.push_back(pt);
    }

    canvas.polyline(pts, data);
  }

  return true;  
}
