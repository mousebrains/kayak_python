<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/class_tiers.php';

/**
 * Unit tests for parse_class_tiers() in php/includes/class_tiers.php.
 * Mirrors src/kayak/utils/class_tiers.py. Pure function — no DB, no HTTP.
 */
final class ClassTiersTest extends TestCase
{
    public function test_null_and_empty_return_empty(): void
    {
        $this->assertSame([], parse_class_tiers(null));
        $this->assertSame([], parse_class_tiers(''));
    }

    public function test_single_tier(): void
    {
        $this->assertSame(['III'], parse_class_tiers('III'));
        $this->assertSame(['I'], parse_class_tiers('I'));
        $this->assertSame(['V'], parse_class_tiers('V'));
    }

    public function test_plus_modifier_is_stripped(): void
    {
        $this->assertSame(['III'], parse_class_tiers('III+'));
        $this->assertSame(['IV'], parse_class_tiers('IV+'));
    }

    public function test_crux_in_parens_is_dropped(): void
    {
        // "III+(IV)" → base tier III only; the (IV) crux is removed.
        $this->assertSame(['III'], parse_class_tiers('III+(IV)'));
        $this->assertSame(['II'], parse_class_tiers('II (III)'));
    }

    public function test_hyphen_range_expands_inclusive(): void
    {
        $this->assertSame(['II', 'III'], parse_class_tiers('II-III'));
        $this->assertSame(['I', 'II', 'III', 'IV', 'V'], parse_class_tiers('I-V'));
    }

    public function test_en_dash_range_expands(): void
    {
        // U+2013 EN DASH is accepted as a range separator just like '-'.
        $this->assertSame(['II', 'III', 'IV'], parse_class_tiers("II\u{2013}IV"));
    }

    public function test_reversed_range_is_normalised(): void
    {
        // "IV-II" → swap so lo <= hi, expand II..IV.
        $this->assertSame(['II', 'III', 'IV'], parse_class_tiers('IV-II'));
    }

    public function test_space_separated_classes_collected(): void
    {
        // "IV V" → both tiers, sorted in Roman order.
        $this->assertSame(['IV', 'V'], parse_class_tiers('IV V'));
    }

    public function test_result_is_deduped_and_sorted(): void
    {
        // Overlapping ranges collapse to a sorted unique tier list.
        $this->assertSame(['II', 'III', 'IV'], parse_class_tiers('II-III, III-IV'));
        $this->assertSame(['III'], parse_class_tiers('III III+ (IV)'));
    }

    public function test_no_roman_numerals_returns_empty(): void
    {
        // Nothing matchable → empty list (loop finds no hits).
        $this->assertSame([], parse_class_tiers('class unknown'));
    }
}
