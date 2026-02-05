3-23-2006

To generate a fresh set of SQL databases run

gen.sql/rebuild # Takes a couple of minutes

which calls

gen.sql/setup # Sets up databases and users
gen.sql/mkParameters # Updates parameters databases
gen.sql/mkURLs # Updates URLparse table by minig files/urls/* files
gen.sql/mkDescription # Updates Description table for use in building description pages
gen.sql/mkFlows | mysql -u root # Updates flow/gage/temperature databases (takes a while)
gen.sql/mkMaster | mysql -u root # Updates master and corrections databases (This references ~/tools/kayaking/db.master)

Functionality implemented:

mkMainPage builds levels.html
mkDescription builds d.htm and d.htm.gz
merger builds merged flow/gage/temperature databases
calculator builds calculator flow/gage/temperature databases
builder builds state specific pages
maps builds level map

fetcher fetch and parse and store web pages, but mostly untested

CGI SCRIPTS:

display is be main script and has the following left to be implemented:
  a) edit
  b) dynamic make description page
  c) dynamice make flow page

TODO:

Take care of fetchPages 
Deal with testing hours selection criteria code

Look at idwr parser

CHECK:
HCR is in HillsCreek.OR.Resv binding
