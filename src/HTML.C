#include <HTML.H>
#include <CompressString.H>
#include <Paths.H>
#include <sstream>
#include <fstream>

const char * HTML::HTML401Strict() {
    return "\
<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01//EN\"\n\
 \"http://www.w3.org/TR/html4/strict.dtd\">\n\
<html>\n\
";
}

const char * HTML::HTML401Transitional() {
    return "\
<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01 Transitional//EN\"\n\
 \"http://www.w3.org/TR/1999/REC-html401-19991224/loose.dtd\">\n\
<html>\n\
";
}

const char * HTML::HTML401Frameset() {
    return "\
<!DOCTYPE HTML PUBLIC \"-//W3C//DTD HTML 4.01 Frameset//EN\"\n\
 \"http://www.w3.org/TR/1999/REC-html401-19991224/frameset.dtd\">\n\
<html>\n\
";
}

const char * HTML::XHTML10Strict() {
    return "\
<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Strict//EN\"\n\
 \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd\">\n\
<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\" lang=\"en\">\n\
";
}

const char * HTML::XHTML10Transitional() {
    return "\
<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Transitional//EN\"\n\
 \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd\">\n\
<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\" lang=\"en\">\n\
";
}

const char * HTML::XHTML10Frameset() {
    return "\
<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.0 Frameset//EN\"\n\
 \"http://www.w3.org/TR/xhtml1/DTD/xhtml1-frameset.dtd\">\n\
<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\" lang=\"en\">\n\
";
}

const char *HTML::XHTML11() {
  return "\
<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
<!DOCTYPE html PUBLIC \"-//W3C//DTD XHTML 1.1//EN\"\n\
   \"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd\">\n\
<html xmlns=\"http://www.w3.org/1999/xhtml\" xml:lang=\"en\">\n\
";
}

HTML::HTML(bool compress,
           bool qText)
  : mOSS(new std::ostringstream),
    mOSF(0), 
    mOSR(0),
    mOS(mOSS),
    mqText(qText),
    mDumped(false),
    mCompress(compress),
    mCompressed(false),
    mHTML(true),
    mBody(false)
{
  if (!qText) {
    *this << XHTML11();
  }
}

HTML::HTML(std::ostream& os, 
           bool compress,
           bool qText)
  : mOSS(0),
    mOSF(0),
    mOSR(&os),
    mOS(mOSR),
    mqText(qText),
    mDumped(false),
    mCompress(compress),
    mCompressed(false),
    mHTML(true),
    mBody(false)
{
  if (compress) {
    mOSS = new std::ostringstream;
    mOS = mOSS;
  }
  if (!qText) { 
    *this << XHTML11();
  }
}

HTML::HTML(const char *filename, 
           bool compress,
           bool qText)
  : mOSS(0),
    mOSF(new std::ofstream(filename)),
    mOSR(0),
    mOS(mOSF),
    mqText(qText),
    mDumped(false),
    mCompress(compress),
    mCompressed(false),
    mHTML(true),
    mBody(false)
{
  if (compress) {
    mOSS = new std::ostringstream;
    mOS = mOSS;
  }
  if (!qText) {
    *this << XHTML11();
  }
}

HTML::~HTML()
{
  if (mBody || mHTML)
    end();

  if (!mDumped && mOSS && (mOSF || mOSR))
    dump(mOSF ? *mOSF : *mOSR);

  delete mOSS;
  delete mOSF;
}

std::ostream&
operator << (std::ostream& os,
	     HTML& h)
{
  h.dump(os);
  return os;
}

bool
HTML::specialDump(std::ostream& os)
{
  if (mOSS) {
    mOSS->seekp(0, std::ios::end);
    os << "length " << mOSS->tellp() << std::endl;
    os << mOSS->str();
    return (os ? true : false);
  }
  return false;
}

bool
HTML::dump(std::ostream& os)
{
  mDumped = (&os == mOSF) || (&os == mOSR);

  end();

  _compress();

  if (mCompressed) {
    os << mPage;
    return true;
  }

  if (mOSS) {
    os << mOSS->str();
    return (os ? true : false);
  }

  return false;
}

std::string::size_type
HTML::length()
{
  end();

  _compress();
 
  if (mCompressed)
    return mPage.size();

  if (mOSS) {
    mOSS->seekp(0, std::ios::end);
    const size_t len(mOSS->tellp());
    // return len - 1; // Take off std::ends
    return len;
  }
  return std::string::npos;
}

void
HTML::_compress()
{
  if (mOSS && mCompress && !mCompressed) {
    mPage = Compress::string(mOSS->str());
    mCompressed = !mPage.empty();
  }
}

void 
HTML::title (const std::string& title,
             const std::string& args) 
{
  if (mOS) {
    startObject("title", args);
    *mOS << title << std::endl;
    endObject("title");
  }
}

void 
HTML::head (const std::string& tit,
            const std::string& args) 
{
  if (mOS) {
    startHead();
    *mOS << "<link rel=\"shortcut icon\" href=\""
         << Paths::DocumentRoot << "zen_favicon.ico\" type=\"image/x-icon\" />";

    title(tit);
    if (!args.empty()) 
      *mOS << args;
    endHead();
    startBody();
  }
}

bool
HTML::startObject (const std::string& object,
                   const std::string& args)
{
  if (mOS) {
    *mOS << '<' << object;
    if (!args.empty()) {
      *mOS << " " << args;
    }
    *mOS << '>' << std::endl;
  }

  return true;
}

void
HTML::end ()
{
  if (mOS) {
    if (mHead) endHead();
    if (mBody) endBody();
    if (mHTML) endHTML();
  }
}

void 
HTML::anchor(const std::string& href, 
             const std::string& body) 
{
  if (mOS)
    anchor(*mOS, href, body);
}

void
HTML::anchor(std::ostream& os,
             const std::string& href, 
             const std::string& body) 
{
    os << "<a href=" << href << ">" << body << "</a>";
}

void 
HTML::errorPage(const std::string& title, 
                const std::string& msg) 
{
  head(title);
  *this << msg << std::endl;
}

void 
HTML::errorPage(std::ostream& os,
                const std::string& title, 
                const std::string& msg) 
{
  HTML h(os);
  h.head(title);
  h << msg << std::endl;
}
