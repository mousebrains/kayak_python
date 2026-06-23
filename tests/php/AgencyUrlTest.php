<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/agency_url.php';

/**
 * Unit tests for php/includes/agency_url.php.
 *
 * agency_attribution_url() maps a source's agency string to a fixed provider
 * homepage for the few local operators that have no per-station page. Pure
 * function — no DB, no $_SERVER.
 */
final class AgencyUrlTest extends TestCase
{
    public function test_null_and_empty_have_no_link(): void
    {
        $this->assertNull(agency_attribution_url(null));
        $this->assertNull(agency_attribution_url(''));
    }

    public function test_unmapped_agency_has_no_link(): void
    {
        $this->assertNull(agency_attribution_url('USGS'));
        $this->assertNull(agency_attribution_url('NWRFC'));
        $this->assertNull(agency_attribution_url('Some Other District'));
    }

    public function test_cowlitz_fd5_maps_to_homepage(): void
    {
        $this->assertSame(
            'https://www.cowlitzfd5.org',
            agency_attribution_url('Cowlitz County Fire District 5'),
        );
    }

    public function test_match_tolerates_location_suffix(): void
    {
        // The stored agency carries a ", Kalama" suffix; substring match still hits.
        $this->assertSame(
            'https://www.cowlitzfd5.org',
            agency_attribution_url('Cowlitz County Fire District 5, Kalama'),
        );
    }

    public function test_match_is_case_insensitive(): void
    {
        $this->assertSame(
            'https://www.cowlitzfd5.org',
            agency_attribution_url('cowlitz county fire district 5'),
        );
    }

    public function test_trailing_digit_does_not_over_match(): void
    {
        // A hypothetical future "...District 50" must NOT inherit FD5's link
        // (the trailing-digit negative lookahead). "District 5" + a non-digit
        // boundary (comma, space, end) still matches.
        $this->assertNull(agency_attribution_url('Cowlitz County Fire District 50'));
        $this->assertNull(agency_attribution_url('Cowlitz County Fire District 51, Woodland'));
        $this->assertSame(
            'https://www.cowlitzfd5.org',
            agency_attribution_url('Cowlitz County Fire District 5'),
        );
    }
}
