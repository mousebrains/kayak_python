#ifndef INC_XML_H_
#define INC_XML_H_

#include <iosfwd>
#include <vector>
#include <libxml/parser.h>
#include <libxml/tree.h>

class XML {
public:
  class Node {
  private:
    xmlNodePtr mNode;
  public:
    Node(xmlNodePtr node) : mNode(node) {}

    Node parent() const {return Node(mNode->parent);}
    Node children() const {return Node(mNode->children);}
    Node next() const {return Node(mNode->next);}

    std::string path() const;
    std::string content() const;
    std::string attribute(const std::string& key) const;
    bool isElement() const;

    bool operator != (const Node& rhs) const {return mNode != rhs.mNode;}
    bool operator == (const Node& rhs) const {return mNode == rhs.mNode;}

    bool operator != (const xmlNodePtr rhs) const {return mNode != rhs;}
    bool operator == (const xmlNodePtr rhs) const {return mNode == rhs;}

    operator bool () const {return mNode;}
  };

private:
  xmlDocPtr mDoc; // Document
  Node mRoot; // Root element
public:
  XML(const std::string& content, const std::string& url);
  ~XML();

  const Node& root() const {return mRoot;}

  class const_iterator {
  private:
    const Node mRoot;
    Node mNode;
  public:
    const_iterator(const Node& root);

    const Node  operator * () const {return mNode;}
    const Node *operator -> () const {return &mNode;}

    bool operator != (const const_iterator rhs) const { return mNode != rhs.mNode; }
    const_iterator& operator ++ ();
  };

  const_iterator begin() const {return const_iterator(mRoot);}
  const_iterator end() const {return const_iterator(Node(0));}
};

std::ostream& operator << (std::ostream& os, const XML::Node& node);
std::ostream& operator << (std::ostream& os, const XML& node);

#endif // INC_XML_H_
