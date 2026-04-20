"""Parse whitewater class strings into a sorted list of base tiers.

Supports the patterns actually present in this DB: single grades (``III``),
ranges (``II-IV``, ``II-V+``), modifiers (``III+``, ``IV-``), parenthetical
cruxes (``III-IV(V)``), and space-separated multi-grades (``II IV``).
Cruxes are intentionally dropped — a paddler filtering for class V wants
runs graded V overall, not III-IV runs with a single V rapid.

Return value is a stable Roman-ordered ``list[str]``; unrecognized or
empty input returns ``[]``.
"""

from __future__ import annotations

import re

_ROMAN_VALUE = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
_ROMAN_REVERSE = {v: k for k, v in _ROMAN_VALUE.items()}

# One token — a Roman grade — with an optional range partner. Ordering the
# alternatives longest-first makes the engine prefer ``III`` over ``II``+``I``.
_TIER_RE = re.compile(r"\b(V|IV|III|II|I)(?:\s*[-\u2013]\s*(V|IV|III|II|I))?\b")

# Grab parenthetical cruxes so we can strip them before tier extraction.
_PAREN_RE = re.compile(r"\([^)]*\)")


def parse_class_tiers(s: str | None) -> list[str]:
    """Return the sorted set of base tiers referenced by *s*.

    >>> parse_class_tiers("III-IV(V)")
    ['III', 'IV']
    >>> parse_class_tiers("II-V+")
    ['II', 'III', 'IV', 'V']
    >>> parse_class_tiers("Flatwater")
    []
    """
    if not s:
        return []
    # Drop cruxes; strip '+' modifiers (same tier bucket) so that e.g.
    # "III+-V" becomes "III-V" and parses as a range.
    cleaned = _PAREN_RE.sub("", s).replace("+", "")
    found: set[int] = set()
    for m in _TIER_RE.finditer(cleaned):
        lo = _ROMAN_VALUE[m.group(1)]
        hi = _ROMAN_VALUE[m.group(2)] if m.group(2) else lo
        if hi < lo:
            lo, hi = hi, lo
        found.update(range(lo, hi + 1))
    return [_ROMAN_REVERSE[v] for v in sorted(found)]
