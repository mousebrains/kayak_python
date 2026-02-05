#include <genPlot.H>
#include <PNGCanvas.H>

int
main (int argc,
      char **argv)
{
  Properties prop;
  prop.background("white").fontAnchor("center").fontSize(16);

  PNGCanvas canvas(800, 500, prop);

  return genPlot(canvas); 
}
