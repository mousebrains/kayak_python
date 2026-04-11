"""Tests for kayak.utils.conversions."""

from datetime import UTC, datetime

from kayak.utils.conversions import (
    TIMEZONE_OFFSETS,
    celsius_to_fahrenheit,
    cm_to_feet,
    interpolate_rating,
    kcfs_to_cfs,
    parse_datetime,
    safe_float,
    safe_int,
)

# ---------------------------------------------------------------------------
# celsius_to_fahrenheit
# ---------------------------------------------------------------------------


class TestCelsiusToFahrenheit:
    def test_freezing_point(self):
        assert celsius_to_fahrenheit(0) == 32.0

    def test_boiling_point(self):
        assert celsius_to_fahrenheit(100) == 212.0

    def test_negative_forty_crossover(self):
        assert celsius_to_fahrenheit(-40) == -40.0

    def test_body_temperature(self):
        assert celsius_to_fahrenheit(37) == 98.6


# ---------------------------------------------------------------------------
# kcfs_to_cfs
# ---------------------------------------------------------------------------


class TestKcfsToCfs:
    def test_one_kcfs(self):
        assert kcfs_to_cfs(1) == 1000.0

    def test_fractional_kcfs(self):
        assert kcfs_to_cfs(2.5) == 2500.0

    def test_zero(self):
        assert kcfs_to_cfs(0) == 0.0


# ---------------------------------------------------------------------------
# cm_to_feet
# ---------------------------------------------------------------------------


class TestCmToFeet:
    def test_zero(self):
        assert cm_to_feet(0) == 0.0

    def test_one_foot(self):
        # 1 foot = 30.48 cm  =>  0.3048 "cm in the API sense"
        # The function does cm * 100 / 2.54 / 12
        # 30.48 * 100 / 2.54 / 12 = 100.0
        assert cm_to_feet(30.48) == 100.0

    def test_small_value(self):
        # 1 cm * 100 / 2.54 / 12 = 3.28083... rounds to 3.3
        assert cm_to_feet(1) == 3.3


# ---------------------------------------------------------------------------
# interpolate_rating
# ---------------------------------------------------------------------------


class TestInterpolateRating:
    def test_empty_table_returns_none(self):
        assert interpolate_rating([], 5.0) is None

    def test_below_min_clamps_to_first(self):
        table = [(1.0, 10.0), (2.0, 20.0)]
        assert interpolate_rating(table, 0.0) == 10.0

    def test_above_max_clamps_to_last(self):
        table = [(1.0, 10.0), (2.0, 20.0)]
        assert interpolate_rating(table, 5.0) == 20.0

    def test_exact_match_first(self):
        table = [(1.0, 10.0), (2.0, 20.0)]
        assert interpolate_rating(table, 1.0) == 10.0

    def test_exact_match_last(self):
        table = [(1.0, 10.0), (2.0, 20.0)]
        assert interpolate_rating(table, 2.0) == 20.0

    def test_midpoint_interpolation(self):
        table = [(0.0, 0.0), (10.0, 100.0)]
        assert interpolate_rating(table, 5.0) == 50.0

    def test_quarter_interpolation(self):
        table = [(0.0, 0.0), (10.0, 100.0)]
        assert interpolate_rating(table, 2.5) == 25.0

    def test_rounding_parameter(self):
        table = [(0.0, 0.0), (10.0, 100.0)]
        # 5.0 -> 50.0, rounding to nearest 10 => 50.0
        assert interpolate_rating(table, 5.0, rounding=10.0) == 50.0
        # 3.0 -> 30.0, rounding to nearest 20 => 40.0
        assert interpolate_rating(table, 3.0, rounding=20.0) == 40.0

    def test_single_entry_below(self):
        table = [(5.0, 50.0)]
        assert interpolate_rating(table, 1.0) == 50.0

    def test_single_entry_above(self):
        table = [(5.0, 50.0)]
        assert interpolate_rating(table, 10.0) == 50.0

    def test_multi_segment(self):
        table = [(0.0, 0.0), (1.0, 10.0), (2.0, 30.0)]
        # Between 1.0 and 2.0: 10 + (30-10)/(2-1) * (1.5-1.0) = 10 + 10 = 20
        assert interpolate_rating(table, 1.5) == 20.0


# ---------------------------------------------------------------------------
# parse_datetime
# ---------------------------------------------------------------------------


class TestParseDatetime:
    def test_iso_space_format(self):
        result = parse_datetime("2024-06-15 10:30:00")
        assert result == datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC)

    def test_iso_t_format(self):
        result = parse_datetime("2024-06-15T10:30:00")
        assert result == datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC)

    def test_slash_format(self):
        result = parse_datetime("06/15/2024 10:30:00")
        assert result == datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC)

    def test_date_only(self):
        result = parse_datetime("2024-06-15")
        assert result == datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC)

    def test_with_timezone_est(self):
        result = parse_datetime("2024-06-15 10:00:00", tz_name="EST")
        # EST is UTC-5, so 10:00 EST = 15:00 UTC
        expected = datetime(2024, 6, 15, 15, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_with_timezone_pst(self):
        result = parse_datetime("2024-06-15 10:00:00", tz_name="PST")
        # PST is UTC-8, so 10:00 PST = 18:00 UTC
        expected = datetime(2024, 6, 15, 18, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_unknown_timezone_treated_as_utc(self):
        result = parse_datetime("2024-06-15 10:00:00", tz_name="BOGUS")
        expected = datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
        assert result == expected

    def test_unknown_timezone_logs_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="kayak.utils.conversions"):
            parse_datetime("2024-06-15 10:00:00", tz_name="BOGUS")
        assert "Unknown timezone 'BOGUS'" in caplog.text

    def test_empty_string_returns_none(self):
        assert parse_datetime("") is None

    def test_whitespace_only_returns_none(self):
        assert parse_datetime("   ") is None

    def test_garbage_returns_none(self):
        assert parse_datetime("not-a-date") is None

    def test_result_is_utc_aware(self):
        result = parse_datetime("2024-01-01")
        assert result is not None
        assert result.tzinfo is UTC


# ---------------------------------------------------------------------------
# safe_float
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_simple_float(self):
        assert safe_float("123.4") == 123.4

    def test_comma_separated(self):
        assert safe_float("1,234.5") == 1234.5

    def test_inf_returns_none(self):
        assert safe_float("inf") is None

    def test_negative_inf_returns_none(self):
        assert safe_float("-inf") is None

    def test_nan_returns_none(self):
        assert safe_float("nan") is None

    def test_empty_string_returns_none(self):
        assert safe_float("") is None

    def test_alpha_returns_none(self):
        assert safe_float("abc") is None

    def test_whitespace_stripped(self):
        assert safe_float("  42.0  ") == 42.0

    def test_integer_string(self):
        assert safe_float("100") == 100.0


# ---------------------------------------------------------------------------
# safe_int
# ---------------------------------------------------------------------------


class TestSafeInt:
    def test_simple_int(self):
        assert safe_int("42") == 42

    def test_whitespace_stripped(self):
        assert safe_int(" 42 ") == 42

    def test_alpha_returns_none(self):
        assert safe_int("abc") is None

    def test_empty_returns_none(self):
        assert safe_int("") is None

    def test_float_string_returns_none(self):
        assert safe_int("3.14") is None

    def test_negative(self):
        assert safe_int("-7") == -7


# ---------------------------------------------------------------------------
# TIMEZONE_OFFSETS
# ---------------------------------------------------------------------------


class TestTimezoneOffsets:
    def test_est_offset(self):
        assert TIMEZONE_OFFSETS["EST"] == -5

    def test_utc_offset(self):
        assert TIMEZONE_OFFSETS["UTC"] == 0

    def test_pst_offset(self):
        assert TIMEZONE_OFFSETS["PST"] == -8
