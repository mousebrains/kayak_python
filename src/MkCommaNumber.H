#ifndef INC_MkCommaNumber_H_
#define INC_MkCommaNumber_H_

#include <string>

std::string MkCommaNumber (const double value, bool handleNaNInf = false);
std::string MkCommaNumber (const double value, int fractionalDigits,
                           bool handleNaNInf = false);

#endif // INC_MkCommaNumber_H_
