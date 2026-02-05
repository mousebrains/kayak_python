#include <HTMLrender.H>
#include <libxml/HTMLparser.h>
#include <iostream>

void
HTMLrender::startElement(const xmlChar *name,
			 const xmlChar **attributes)
{
  mStack.push_back(mAction);

  tActions::const_iterator it(mActions.find(std::string((const char *) name)));
  tAction act(it == mActions.end() ? mAction : it->second);

  switch (act) {
  case BAR: mText += " | "; break;
  case NEWLINE: mText += "\n"; break;
  case SKIP: mAction = act; break;
  case RECORD: mAction = act; break;
  case DENEWLINE: mAction = act; break;
  };
}

void
HTMLrender::endElement(const xmlChar *name)
{
  if (!mStack.empty()) {
    tStack::iterator it(mStack.end()); // past end of list
    --it; // backup up to last element
    mAction = *it;
    mStack.erase(it);
  } else {
    mAction = SKIP;
  }
}

void
HTMLrender::characters(const xmlChar *chars,
		       int length)
{
  if (mAction == RECORD)
    mText.append((const char *) chars, length);
  else if (mAction == DENEWLINE) {
    std::string str((const char *) chars, length);
    
    for (std::string::size_type i = 0; i < str.size(); ++i)
      if (str[i] == '\n')
	str[i] = ' ';
    
    mText.append(str);
  }
}

HTMLrender::HTMLrender(const std::string& page)
  : mAction(SKIP)
{
  static htmlSAXHandler saxHandler = {
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    startElement, endElement, 0,
    characters, 0, 0, 0, 0, 0, 0, 0,
    characters, 0};

  mActions.insert(std::make_pair("html", RECORD));
  mActions.insert(std::make_pair("head", SKIP));
  mActions.insert(std::make_pair("title", SKIP));
  mActions.insert(std::make_pair("body", RECORD));
  mActions.insert(std::make_pair("pre", RECORD));
  mActions.insert(std::make_pair("p", NEWLINE));
  mActions.insert(std::make_pair("ul", NEWLINE));
  mActions.insert(std::make_pair("ol", NEWLINE));
  mActions.insert(std::make_pair("h1", NEWLINE));
  mActions.insert(std::make_pair("h2", NEWLINE));
  mActions.insert(std::make_pair("h3", NEWLINE));
  mActions.insert(std::make_pair("li", NEWLINE));
  mActions.insert(std::make_pair("br", NEWLINE));
  mActions.insert(std::make_pair("table", DENEWLINE));
  mActions.insert(std::make_pair("tr", NEWLINE));
  mActions.insert(std::make_pair("th", BAR));
  mActions.insert(std::make_pair("td", BAR));

  htmlParserCtxtPtr ctxt(htmlCreatePushParserCtxt(&saxHandler, this, "", 0, "",
						  XML_CHAR_ENCODING_NONE));
  
  htmlParseChunk(ctxt, page.c_str(), page.size(), 0);
  htmlParseChunk(ctxt, "", 0, 1);
  htmlFreeParserCtxt(ctxt);
}
