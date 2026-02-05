TARGETS = all distclean clean install 

.PHONY: $(TARGETS)

$(TARGETS):
	$(MAKE) -C src $@
	$(MAKE) -C scripts $@
