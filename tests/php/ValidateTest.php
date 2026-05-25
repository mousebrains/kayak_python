<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../php/includes/validate.php';

/**
 * Unit tests for the input-validation primitives in php/includes/validate.php.
 * Pure functions — no DB, no HTTP.
 */
final class ValidateTest extends TestCase
{
    // --- validate_date ----------------------------------------------------

    public function test_valid_date_round_trips(): void
    {
        $this->assertSame('2026-05-25', validate_date('2026-05-25'));
        $this->assertSame('2024-02-29', validate_date('2024-02-29')); // leap day
    }

    public function test_null_false_empty_return_null(): void
    {
        // filter_input() yields string|false|null — all the not-a-string
        // cases (and the empty string) must map to null.
        $this->assertNull(validate_date(null));
        $this->assertNull(validate_date(false));
        $this->assertNull(validate_date(''));
    }

    public function test_wrong_format_returns_null(): void
    {
        $this->assertNull(validate_date('2026-5-25'));    // unpadded month
        $this->assertNull(validate_date('05/25/2026'));   // slashes
        $this->assertNull(validate_date('2026-05-25T00')); // trailing junk
        $this->assertNull(validate_date('not-a-date'));
    }

    public function test_impossible_calendar_date_returns_null(): void
    {
        // Right shape, but checkdate() rejects it.
        $this->assertNull(validate_date('2026-02-30')); // Feb 30
        $this->assertNull(validate_date('2026-13-01')); // month 13
        $this->assertNull(validate_date('2026-00-10')); // month 0
        $this->assertNull(validate_date('2025-02-29')); // non-leap year
    }

    // --- date_ts ----------------------------------------------------------

    public function test_date_ts_parses_iso_date(): void
    {
        // strtotime of a midnight date in the server TZ; compare against the
        // same expression so the test is TZ-agnostic.
        $this->assertSame(strtotime('2026-05-25'), date_ts('2026-05-25'));
        $this->assertIsInt(date_ts('2000-01-01'));
    }

    public function test_date_ts_falls_back_to_now_on_unparseable(): void
    {
        // Defensive branch: an unparseable string yields a sane "now"-ish int
        // rather than false. Allow a generous window for slow CI.
        $before = time();
        $ts = date_ts('totally not a date');
        $after = time();
        $this->assertGreaterThanOrEqual($before, $ts);
        $this->assertLessThanOrEqual($after + 1, $ts);
    }
}
