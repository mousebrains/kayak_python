#include <XML.H>
#include <iostream>

namespace {
  std::string freeString(const char *ptr) {
    if (ptr) {
      const std::string str(ptr);
      free((void *) ptr);
      return str;
    }
    return std::string();
  }
}

XML::XML(const std::string& content,
         const std::string& url)
  : mDoc(xmlReadMemory(content.c_str(), content.size(), url.c_str(), 0, 0)),
    mRoot(xmlDocGetRootElement(mDoc))
{
  LIBXML_TEST_VERSION; // Make sure there is not a version mismatch

  if (!mDoc)
    throw "Error parsing in memory XML document";

  if (!mRoot)
    throw "No root element found for in memory XML document";
}

XML::~XML()
{
  xmlFreeDoc(mDoc); // Free up this document
  xmlCleanupParser(); // Cleanup the XML library
  xmlMemoryDump(); // for debugging memory for regression tests
}

XML::const_iterator::const_iterator(const Node& root)
  : mRoot(root),
    mNode(root)
{
}

XML::const_iterator&
XML::const_iterator::operator ++ ()
{
  do {
    if (!mNode) // Anything else to do?
      break; // Nope

    if (mNode == mRoot.parent()) { // Don't go below the root
      mNode = 0;
      break;
    }

    if (mNode.children()) { // Look for kids
      mNode = mNode.children();
      continue;
    }

    if (mNode.next()) { // Other nodes at this level
     mNode = mNode.next();
     continue;
    }

    while (mNode.parent() && (mNode != mRoot.parent())) {
      mNode = mNode.parent();
      if (mNode.next()) {
        mNode = mNode.next();
        break;
      }
    }
    if (!mNode.parent() || mNode == mRoot.parent()) {
      mNode = 0;
      break;
    }
  } while (!mNode.isElement());

  return *this;
}

std::string
XML::Node::path() const
{
  return freeString((const char *) xmlGetNodePath(mNode));
}

std::string
XML::Node::content() const
{
  return freeString((const char *) xmlNodeGetContent(mNode));
}

std::string
XML::Node::attribute(const std::string& key) const
{
  return freeString((const char *) xmlGetProp(mNode, BAD_CAST key.c_str()));
}

bool
XML::Node::isElement() const
{
  return mNode->type == XML_ELEMENT_NODE;
}

std::ostream& 
operator << (std::ostream& os, 
             const XML::Node& node)
{
  os << node.path() << " -> " << node.content();

  return os;
}

std::ostream& 
operator << (std::ostream& os, 
             const XML& xml)
{
  for (XML::const_iterator it = xml.begin(); it != xml.end(); ++it) 
    os << *it << std::endl;

  return os;
}
