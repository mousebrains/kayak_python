#include <Display.H>

// Construct actual web pages

int
main (int argc,
      char **argv)
{
  Display display("Builder", "no_show is null and db_name is not null");

  display.csv(std::string());
  display.text(std::string());
  display.html(std::string());

  const Display::tStates& states(display.states());

  for (Display::tStates::const_iterator et(states.end()), it(states.begin()); it != et; ++it) {
    display.csv(*it);
    display.text(*it);
    display.html(*it);
  }

  return 0;
}
