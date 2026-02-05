#include <MkCommaNumber.H>
#include <cmath>
#include <cstdio>
#include <math.h>

std::string
MkCommaNumber (const double value,
                  int fractionalDigits,
                  bool handleNaNInf)
{
  static const double multiplier[] = { 
	1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12 };

  if (handleNaNInf) {
    if (std::isnan(value))
      return "NaN";
    else if (!finite(value))
      return value > 0 ? "Inf" : "-Inf";
  }

  if (fractionalDigits <= 0)
    return MkCommaNumber(value);

  if (fractionalDigits > 12)
    fractionalDigits = 12;

  const double mult(multiplier[fractionalDigits]);
  const double blownup(rint(std::fabs(value) * mult));
  const double wholeValue(floor(blownup / mult));
  const int fraction(static_cast<int>(rint(blownup - wholeValue * mult)));
  std::string number(MkCommaNumber(wholeValue));

  const int len(64);
  if ((len - 5) >= fractionalDigits) {
    char buffer[len];
    snprintf(buffer, sizeof(buffer), ".%0*d", fractionalDigits, fraction);
    number += buffer;
  } else
    number += ".?";

  return (value < 0) ? ("-" + number) : number;
}

std::string
MkCommaNumber (const double value, bool handleNaNInf)
{
  if (handleNaNInf) {
    if (std::isnan(value))
      return "NaN";
    else if (!finite(value))
      return value > 0 ? "Inf" : "-Inf";
  }

  double val(fabs(rint(value)));

  if (val == 0) 
    return (value < 0) ? "-0" : "0";

  if (val > 1e50)
    return (value < 0) ? "-?" : "?";

  std::string line;

  for (; val >= 1000.; val = val / 1000.) {
    const int remainder(static_cast<int>(std::fmod(val, 1000.)));
    char buffer[4];
    sprintf(buffer, "%3.3d", remainder);
    if (!line.empty())
      line.insert(0, ",");
    line.insert(0, buffer);
  }
  if (val > 0) {
    char buffer[4];
    sprintf(buffer, "%d", static_cast<int>(val));
    if (!line.empty())
      line.insert(0, ",");
    line.insert(0, buffer);
  }
  return (value < 0) ? ("-" + line) : line;
}
