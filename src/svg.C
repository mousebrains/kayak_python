#include <genPlot.H>
#include <SVGCanvas.H>

int
main (int argc,
      char **argv)
{
  Properties prop;
  prop.background("white").fontAnchor("center");
  SVGCanvas canvas("plot", "800px", "500px", prop);

  return genPlot(canvas); 
}
