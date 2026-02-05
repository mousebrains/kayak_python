#ifndef INC_GIF_H_
#define INC_GIF_H_

#include <string>
#include <vector>

class GifFileType;
class GifColorType;

class GIF {
private:
  const std::string mFilename;
  GifFileType *mHandle;
  
  void cleanup();
public:
  GIF(const std::string& basename);
  ~GIF();

  operator bool () const {return mHandle;}

  bool isGray() const;
  bool isGray(const GifColorType &color) const;

  bool comb(const std::string& filename);

  bool dump(const std::string& filename);

  bool dumpPNG(const std::string& filename);
};

#endif // INC_GIF_H_
