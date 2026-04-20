"""Validate the class-tier parser against the strings present in the live DB
plus synthetic edge cases. Cruxes are dropped; ranges expand inclusively."""

from __future__ import annotations

import pytest

from kayak.utils.class_tiers import parse_class_tiers

# Parametric cases covering every distinct `reach_class.name` value in the
# live DB. Keeping the table next to the parser means future contributors
# see which real-world strings must keep parsing correctly.
DB_CASES: list[tuple[str, list[str]]] = [
    ("Flatwater", []),
    ("I", ["I"]),
    ("I II", ["I", "II"]),
    ("I VI", ["I"]),  # VI not supported; keep the I we can parse.
    ("I(II)", ["I"]),
    ("I(IV)", ["I"]),
    ("I+", ["I"]),
    ("I+(II)", ["I"]),
    ("I-II", ["I", "II"]),
    ("I-II(III)", ["I", "II"]),
    ("I-III", ["I", "II", "III"]),
    ("I-III(IV)", ["I", "II", "III"]),
    ("I-IV", ["I", "II", "III", "IV"]),
    ("II", ["II"]),
    ("II III", ["II", "III"]),
    ("II IV", ["II", "IV"]),
    ("II(III)", ["II"]),
    ("II(IV)", ["II"]),
    ("II(V)", ["II"]),
    ("II+", ["II"]),
    ("II+(III)", ["II"]),
    ("II+(IV)", ["II"]),
    ("II-III", ["II", "III"]),
    ("II-III(IV)", ["II", "III"]),
    ("II-III(V)", ["II", "III"]),
    ("II-III+", ["II", "III"]),
    ("II-III+(IV)", ["II", "III"]),
    ("II-III+(V)", ["II", "III"]),
    ("II-IV", ["II", "III", "IV"]),
    ("II-IV(V)", ["II", "III", "IV"]),
    ("II-IV+", ["II", "III", "IV"]),
    ("II-IV+(V)", ["II", "III", "IV"]),
    ("II-V", ["II", "III", "IV", "V"]),
    ("II-V+", ["II", "III", "IV", "V"]),
    ("III", ["III"]),
    ("III IV", ["III", "IV"]),
    ("III(IV)", ["III"]),
    ("III(IV-)", ["III"]),
    ("III+", ["III"]),
    ("III+(IV)", ["III"]),
    ("III+(V)", ["III"]),
    ("III+-V", ["III", "IV", "V"]),
    ("III-IV", ["III", "IV"]),
    ("III-IV(V)", ["III", "IV"]),
    ("III-IV(V+)", ["III", "IV"]),
    ("III-IV+", ["III", "IV"]),
    ("III-IV+(V)", ["III", "IV"]),
    ("III-IV+(V+)", ["III", "IV"]),
    ("III-V", ["III", "IV", "V"]),
    ("III-V(V+)", ["III", "IV", "V"]),
    ("IV", ["IV"]),
    ("IV V", ["IV", "V"]),
    ("IV(V)", ["IV"]),
    ("IV(V+)", ["IV"]),
    ("IV+", ["IV"]),
    ("IV-V", ["IV", "V"]),
    ("IV-V(V+)", ["IV", "V"]),
    ("V", ["V"]),
    ("V+", ["V"]),
]


@pytest.mark.parametrize(("raw", "expected"), DB_CASES)
def test_parse_class_tiers_live_db_values(raw: str, expected: list[str]) -> None:
    assert parse_class_tiers(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        (None, []),
        ("   ", []),
        ("II\u2013III", ["II", "III"]),  # Unicode en-dash.
        (" III-IV ", ["III", "IV"]),  # Surrounding whitespace.
        ("class III", ["III"]),  # Prose tolerated.
        ("V-II", ["II", "III", "IV", "V"]),  # Reversed range still works.
    ],
)
def test_parse_class_tiers_edges(raw: str | None, expected: list[str]) -> None:
    assert parse_class_tiers(raw) == expected


def test_tiers_are_roman_sorted() -> None:
    # Regression: tiers must always emerge in Roman-numeral order, not
    # alphabetical (which would shuffle IV before V incorrectly).
    assert parse_class_tiers("V II IV III I") == ["I", "II", "III", "IV", "V"]
