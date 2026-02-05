BINDIR = /home/tpw/local/bin
PKGCONFIGDIR = /home/tpw/local/lib/pkgconfig
TEMPLATEDIR = /home/tpw/kayaking/web.templates
WEBDIR = ~/public_html
CGIDIR = $(WEBDIR)/cgi

PKGCONFIG = PKG_CONFIG_PATH=$(PKGCONFIGDIR)

MYSQLPPROOT = $(shell if [ -d /home/tpw/local ] ; then echo /home/tpw/local; else echo /usr/local/mysql; fi)
MYSQLPPINC = $(MYSQLPPROOT)/include
MYSQLPPLIB = $(MYSQLPPROOT)/lib

CXXFLAGS += -Wall -pedantic -MD -Wno-long-long
CXXFLAGS += -g -O
# CXXFLAGS += -std=c++17
CXXFLAGS += -std=c++20

CXXFLAGS += -I.
CXXFLAGS += -I$(MYSQLPPINC)
CXXFLAGS += $(shell mysql_config --cflags)
CXXFLAGS += $(shell xml2-config --cflags)
CXXFLAGS += $(shell curl-config --cflags)
CXXFLAGS += $(shell $(PKGCONFIG) freetype-config --cflags)

# LDFLAGS += -Wl,-rpath $(shell mysql_config --libs)


LIBS += -L$(MYSQLPPLIB) -lgif -lpng -lz
LIBS += -lmysqlpp -Wl,-rpath $(MYSQLPPLIB)
LIBS += $(shell mysql_config --libs)
LIBS += $(shell xml2-config --libs)
LIBS += $(shell curl-config --libs)
LIBS += $(shell $(PKGCONFIG) freetype-config --libs)
LIBS += -Bstatic

PROGRAMS = mkMergedMaster mkMainPage mkDescription merger calculator genJS builder maps fetcher cleanOutCorrections sparsifyData
PROGRAMS += calcRating
PROGRAMS += dumpData dumpMaster unused
CGI = display printenv data svg png submit approve authenticate makePage picker
PLAY = 

.PHONY: all distclean clean install install.programs install.web install.cgi install.images

all: $(PROGRAMS) $(CGI)

obj/.created:
	mkdir -p obj
	touch $@

obj/%.o: %.C obj/.created
	$(CXX) $(CXXFLAGS) -c -o $@ $<

mkFlows merger calculator maps cleanOutCorrections sparsifyData dbBrowse play dumpMaster dumpData: obj/libKayak.a
calcRating: obj/libKayak.a
unused: obj/libKayak.a
mkMergedMaster mkMainPage mkDescription genJS builder: obj/libKayak.a obj/libHTML.a
fetcher: obj/libParse.a obj/libKayak.a
display: obj/libCMD.a obj/libHTML.a obj/libKayak.a
svg png: obj/genPlot.o obj/libHTML.a obj/libKayak.a obj/libPlot.a
data: obj/libHTML.a obj/libKayak.a
approve authenticate submit picker makePage printenv: obj/libHTML.a obj/libKayak.a
maps: obj/libHTML.a

mkFlows $(PROGRAMS) $(CGI) $(PLAY): %: obj/%.o
	$(LINK.cc) -o $@ $+ $(LIBS)

install: install.programs install.web

install.programs: $(PROGRAMS)
	install -d $(BINDIR)
	install -p -b $+ $(BINDIR)

install.web: install.cgi install.images install.js install.css install.errors install.site install.html
	install -d $(WEBDIR)      
	install -p -b -T $(TEMPLATEDIR)/main.htaccess $(WEBDIR)/.htaccess
	install -pDbt $(WEBDIR) $(TEMPLATEDIR)/robots.txt $(TEMPLATEDIR)/crossdomain.xml

install.cgi: $(CGI)
	install -d $(CGIDIR)      
	# install -pDbt $(CGIDIR) $(TEMPLATEDIR)/cgi/*
	install -p -b -T $(TEMPLATEDIR)/cgi/cgi.htaccess $(CGIDIR)/.htaccess
	install -p -b $+ $(CGIDIR)

install.js:
	install -d $(WEBDIR)/js
	install -pDbt $(WEBDIR)/js $(TEMPLATEDIR)/js/*

install.css:
	install -d $(WEBDIR)/css 
	install -pDbt $(WEBDIR)/css $(TEMPLATEDIR)/css/*

install.errors:
	install -d $(WEBDIR)/errors 
	install -pDbt $(WEBDIR)/errors $(TEMPLATEDIR)/errors/*

install.site:
	install -d $(WEBDIR)/site 
	# install -pDbt $(WEBDIR)/site $(TEMPLATEDIR)/site/*

install.images:
	install -d $(WEBDIR)
	install -pDbt $(WEBDIR) $(TEMPLATEDIR)/*.gif $(TEMPLATEDIR)/*.ico $(TEMPLATEDIR)/*.png

install.html:
	install -d $(WEBDIR)
	install -pDbt $(WEBDIR) $(TEMPLATEDIR)/acknowledgements.html $(TEMPLATEDIR)/kayak.html
	
obj/libParse.a: obj/Parse.o obj/Parse_CBRFC.o obj/Parse_NOAA.o \
	obj/Parse_NWRFC.o obj/Parse_NWRFC_XML.o \
	obj/Parse_OCS.o obj/Parse_Waves.o obj/Parse_USACE_Ca.o obj/Parse_USBR.o \
	obj/Parse_USGS.o obj/Parse_USBR_Special.o obj/Parse_USBR_PN.o obj/Parse_IDWR.o \
	obj/Parse_Wa_Gov.o obj/Parse_USACE_Resv.o obj/Parse_USACE_Outflow.o \
	obj/Parse_NOAA_XML.o obj/Parse_NOAA2.o obj/Parse_IdahoPower.o
	$(AR) rsv $@ $+

obj/libKayak.a: obj/Display.o obj/Tokenize.o obj/afstream.o obj/ReadFile.o \
		obj/String.o obj/File.o obj/Directory.o obj/RegExp.o \
		obj/isNewer.o obj/ParameterDB.o obj/MyDB.o obj/InfoDB.o \
		obj/DataDB.o obj/Convert.o obj/Curl.o obj/Env.o \
		obj/HTMLrender.o obj/pstream.o obj/Description.o \
		obj/URLparse.o obj/Builder.o obj/XML.o obj/TimeZone.o \
		obj/PageDB.o obj/MakeDescription.o obj/GIF.o obj/Paths.o \
		obj/PageArchive.o
	$(AR) rsv $@ $+

obj/libCMD.a: obj/CMD.description.o obj/CMD.page.o obj/CMD.file.o obj/CMD.plot.o \
	obj/CMD.loadMaster.o obj/CMD.badHash.o obj/CMD.edit.o obj/CMD.view.o
	$(AR) rsv $@ $+

obj/libPlot.a: obj/Canvas.o obj/Stroke.o obj/Point.o obj/Points.o obj/BitMapCanvas.o \
	obj/PNGCanvas.o obj/SVGCanvas.o obj/Transform.o obj/Properties.o \
	obj/RGB.o obj/Axis.o obj/MakePlot.o
	$(AR) rsv $@ $+

obj/libHTML.a: obj/CompressString.o obj/HTML.o obj/HTTP.o obj/CGI.o obj/URL.decode.o \
	obj/URL.encode.o obj/MkCommaNumber.o
	$(AR) rsv $@ $+

distclean clean:
	$(RM) $(PROGRAMS) $(CGI) $(PLAY) core *~
	$(RM) -rf obj

-include obj/*.d
