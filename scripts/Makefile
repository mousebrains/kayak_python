BINDIR = /home/tpw/local/bin

PROGRAMS = master

.PHONY: all clean distclean install 

all: $(PROGRAMS)

install: $(PROGRAMS)
	install -d $(BINDIR)
	install -p -b $+ $(BINDIR)

distclean clean:
	$(RM) core *.o *.d *.a *~
