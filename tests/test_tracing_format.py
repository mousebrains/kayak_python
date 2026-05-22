"""Tests for kayak.tracing.format — reach.geom on-disk contract."""

from __future__ import annotations

import pytest

from kayak.tracing.format import (
    format_geom_for_sql,
    has_wkt_wrapper,
    parse_geom_string,
    validate_lat_lon,
)


def test_validate_lat_lon_accepts_inside_range() -> None:
    validate_lat_lon(44.1, -122.1)
    validate_lat_lon(0.0, 0.0)
    validate_lat_lon(-90.0, -180.0)
    validate_lat_lon(90.0, 180.0)


def test_validate_lat_lon_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="latitude"):
        validate_lat_lon(90.5, 0)
    with pytest.raises(ValueError, match="latitude"):
        validate_lat_lon(-90.1, 0)
    with pytest.raises(ValueError, match="longitude"):
        validate_lat_lon(0, 181)
    with pytest.raises(ValueError, match="longitude"):
        validate_lat_lon(0, -180.5)


def test_format_geom_emits_lon_first_no_wrapper() -> None:
    coords = [(44.104762, -122.021835), (44.105745, -122.020467)]
    out = format_geom_for_sql(coords)
    # lon-first, no LINESTRING, comma-separated, single space between
    # lon and lat, no leading paren.
    assert out == "-122.021835 44.104762,-122.020467 44.105745"
    assert not has_wkt_wrapper(out)


def test_format_geom_rounds_to_precision_default_6() -> None:
    # 7th-decimal noise drops cleanly.
    out = format_geom_for_sql([(44.10476299, -122.02183549)])
    assert out == "-122.021835 44.104763"


def test_format_geom_custom_precision() -> None:
    out = format_geom_for_sql([(44.123456789, -122.123456789)], precision=3)
    assert out == "-122.123 44.123"


def test_format_geom_validates_each_coord() -> None:
    # Bad point sits at index 1 — the message should say so + abort
    # before emitting a partial string.
    with pytest.raises(ValueError, match=r"coord 1.*latitude"):
        format_geom_for_sql([(44.0, -122.0), (95.0, 0.0)])


def test_format_geom_rejects_empty_list() -> None:
    with pytest.raises(ValueError, match="empty"):
        format_geom_for_sql([])


def test_parse_geom_round_trips() -> None:
    coords = [(44.10, -122.10), (44.11, -122.11), (44.12, -122.12)]
    s = format_geom_for_sql(coords)
    parsed = parse_geom_string(s)
    # parse returns (lon, lat) tuples — matches on-disk lon-first order.
    assert parsed == [(-122.10, 44.10), (-122.11, 44.11), (-122.12, 44.12)]


def test_parse_geom_tolerates_whitespace_around_pairs() -> None:
    # PHP's parser does trim($pair); ours should too.
    assert parse_geom_string("-122.0 44.0,  -122.1 44.1  ,-122.2 44.2") == [
        (-122.0, 44.0),
        (-122.1, 44.1),
        (-122.2, 44.2),
    ]


def test_parse_geom_rejects_wrong_arity() -> None:
    with pytest.raises(ValueError, match="expected 'lon lat'"):
        parse_geom_string("-122.0 44.0,broken,-122.2 44.2")


def test_parse_geom_rejects_out_of_range() -> None:
    with pytest.raises(ValueError, match="latitude"):
        parse_geom_string("-122.0 44.0,-122.1 999.0")


def test_parse_geom_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_geom_string("")


def test_has_wkt_wrapper_catches_linestring() -> None:
    assert has_wkt_wrapper("LINESTRING(-122 44, -122.1 44.1)")
    assert has_wkt_wrapper("linestring(-122 44)")
    assert has_wkt_wrapper("  LINESTRING(...)  ")  # leading whitespace OK


def test_has_wkt_wrapper_catches_other_wkt_types() -> None:
    assert has_wkt_wrapper("MULTILINESTRING(...)")
    assert has_wkt_wrapper("POINT(0 0)")
    assert has_wkt_wrapper("POLYGON(...)")
    assert has_wkt_wrapper("MULTIPOINT(...)")
    assert has_wkt_wrapper("GEOMETRYCOLLECTION(...)")


def test_has_wkt_wrapper_catches_bare_paren() -> None:
    # A naked '(' is unambiguously not a "lon lat" pair — flag it.
    assert has_wkt_wrapper("(-122 44, -122.1 44.1)")


def test_has_wkt_wrapper_passes_canonical_format() -> None:
    assert not has_wkt_wrapper("-122.021835 44.104762,-122.020467 44.105745")
    assert not has_wkt_wrapper("")
    assert not has_wkt_wrapper("  -122 44, -122 44  ")
