<?php
declare(strict_types=1);
/**
 * Sanity-check helpers for proposed reach edits.
 *
 * Each check_* function returns a list of issues. Each issue is:
 *   ['level' => 'error'|'warning', 'field' => string, 'message' => string]
 *
 * Errors block submission; warnings are shown to the user and carried
 * into /review.php so the maintainer sees the borderline case.
 */

/**
 * Normalize a river or reach name for loose matching.
 *
 * Lowercases, strips punctuation, drops common hydronyms
 * (river/creek/fork/branch/brook/run/slough), collapses whitespace.
 */
function normalize_name(string $s): string {
    $s = strtolower($s);
    $s = preg_replace('/[^\p{L}\p{N}\s]+/u', ' ', $s);
    $s = preg_replace('/\b(river|creek|fork|branch|brook|run|slough|the|of)\b/u', ' ', $s);
    $s = preg_replace('/\s+/', ' ', trim((string)$s));
    return (string)$s;
}

// ---------------------------------------------------------------------------
// Display name
// ---------------------------------------------------------------------------

function check_display_name(string $proposed, ?string $river): array {
    $issues = [];
    $proposed = trim($proposed);
    if ($proposed === '') {
        return [['level' => 'error', 'field' => 'display_name',
                 'message' => 'Display name cannot be empty.']];
    }
    if (strlen($proposed) > 128) {
        $issues[] = ['level' => 'error', 'field' => 'display_name',
                     'message' => 'Display name must be 128 characters or fewer.'];
    }
    if ($river !== null && trim($river) !== '') {
        $np = normalize_name($proposed);
        $nr = normalize_name($river);
        if ($nr !== '' && !str_contains($np, $nr)) {
            $issues[] = ['level' => 'error', 'field' => 'display_name',
                         'message' => "Display name must include the river name (\"$river\"). "
                                    . "Prefixes like NF, SF, Upper, Oak Fork are fine."];
        }
    }
    return $issues;
}

// ---------------------------------------------------------------------------
// Free-form text
// ---------------------------------------------------------------------------

function check_text_length(string $field, string $value, int $max): array {
    if (strlen($value) > $max) {
        return [['level' => 'error', 'field' => $field,
                 'message' => "$field must be $max characters or fewer (got " . strlen($value) . ")."]];
    }
    return [];
}

function strip_html_tags(string $s): string {
    // Strip HTML tags but preserve legit user text like "<3", "< y", or
    // "<foo@bar.com>". PHP's native strip_tags eats everything between a
    // `<` and the next `>`, so "I love <3 boats" becomes "I love ".
    //
    // We only strip sequences that look like real HTML tags:
    //   <tag>, <tag attr>, <tag ... />, </tag>, <!-- comment -->
    // A tag name must be a letter followed by alphanumerics. Attributes (if
    // any) must start with whitespace; the run-up to > cannot contain `<`
    // or `>`. This leaves "<3" alone (3 is not a letter) and
    // "<foo@bar.com>" alone (`@` is neither whitespace nor `>` after `foo`).
    //
    // Loops to stable fixed point (bounded) so split-tag reassembly attacks
    // like "<scr<script>ipt>" collapse all the way.
    for ($i = 0; $i < 5; $i++) {
        $before = $s;
        $s = preg_replace('/<!--.*?-->/s', '', $s);
        $s = preg_replace('/<\/?[a-zA-Z][a-zA-Z0-9]*(?:\s[^<>]*)?\/?>/', '', $s);
        if ($s === $before) break;
    }
    return trim($s);
}

// ---------------------------------------------------------------------------
// Whitewater class
// ---------------------------------------------------------------------------

/**
 * Validate a whitewater class string. Matches patterns seen in the live
 * data: "III", "III+", "II-III", "IV V", "III+(IV)", "V.1".
 */
function check_class_string(string $field, string $value): array {
    $v = trim($value);
    if ($v === '') {
        return [['level' => 'error', 'field' => $field,
                 'message' => 'Class cannot be empty.']];
    }
    if (strlen($v) > 32) {
        return [['level' => 'error', 'field' => $field,
                 'message' => 'Class must be 32 characters or fewer.']];
    }
    $pat = '/^(?:[IVX]{1,4}(?:\.\d)?[+\-]?)'
         . '(?:[\s\-(,][IVX]{1,4}(?:\.\d)?[+\-]?\)?)?$/';
    if (!preg_match($pat, $v)) {
        return [['level' => 'warning', 'field' => $field,
                 'message' => "\"$v\" doesn't match the expected class format "
                            . '(e.g. III, III+, II-III, IV V, III+(IV), V.1).']];
    }
    return [];
}

// ---------------------------------------------------------------------------
// Flow range (single low/high applied to all reach_class rows)
// ---------------------------------------------------------------------------

/**
 * Validate a single flow range (low, high, data_type).
 *
 * - low <= high.
 * - CFS values in [0, 200000], gauge-ft in [-20, 100].
 */
function check_flow_range(?float $low, ?float $high, string $data_type = 'flow'): array {
    $issues = [];
    $ranges = ['flow' => [0, 200000], 'gauge' => [-20, 100],
               'inflow' => [0, 200000], 'temperature' => [-20, 120]];

    if ($low !== null && $high !== null && $low > $high) {
        $issues[] = ['level' => 'error', 'field' => 'flow_range',
                     'message' => "Low must be \u{2264} high ($low > $high)."];
    }
    [$rmin, $rmax] = $ranges[$data_type] ?? [0, 200000];
    foreach (['low' => $low, 'high' => $high] as $which => $v) {
        if ($v !== null && ($v < $rmin || $v > $rmax)) {
            $issues[] = ['level' => 'warning', 'field' => 'flow_range',
                         'message' => "$which = $v is outside $data_type range [$rmin, $rmax]."];
        }
    }
    return $issues;
}

// ---------------------------------------------------------------------------
// Coordinates
// ---------------------------------------------------------------------------

function _haversine_mi(float $lat1, float $lon1, float $lat2, float $lon2): float {
    $R = 3958.8; // miles
    $dlat = deg2rad($lat2 - $lat1);
    $dlon = deg2rad($lon2 - $lon1);
    $a = sin($dlat / 2) ** 2
       + cos(deg2rad($lat1)) * cos(deg2rad($lat2)) * sin($dlon / 2) ** 2;
    return 2 * $R * asin(min(1.0, sqrt($a)));
}

function check_coords(
    string $field,
    ?float $lat,
    ?float $lon,
    ?float $ref_lat = null,
    ?float $ref_lon = null
): array {
    $issues = [];
    if ($lat === null && $lon === null) return $issues;
    if ($lat === null || $lon === null) {
        return [['level' => 'error', 'field' => $field,
                 'message' => "$field: supply both latitude and longitude, or neither."]];
    }
    if ($lat < -90 || $lat > 90) {
        $issues[] = ['level' => 'error', 'field' => $field,
                     'message' => "$field latitude $lat out of range [-90, 90]."];
    }
    if ($lon < -180 || $lon > 180) {
        $issues[] = ['level' => 'error', 'field' => $field,
                     'message' => "$field longitude $lon out of range [-180, 180]."];
    }
    if ($ref_lat !== null && $ref_lon !== null && $issues === []) {
        $mi = _haversine_mi($lat, $lon, $ref_lat, $ref_lon);
        if ($mi > 100) {
            $issues[] = ['level' => 'error', 'field' => $field,
                         'message' => sprintf('%s is %.0f mi from the current location — refusing.', $field, $mi)];
        } elseif ($mi > 10) {
            $issues[] = ['level' => 'warning', 'field' => $field,
                         'message' => sprintf('%s is %.1f mi from the current location.', $field, $mi)];
        }
    }
    return $issues;
}

/** Check that put-in and take-out are plausibly a single run. */
function check_putin_takeout(
    ?float $lat_s, ?float $lon_s, ?float $lat_e, ?float $lon_e
): array {
    if ($lat_s === null || $lon_s === null || $lat_e === null || $lon_e === null) return [];
    $mi = _haversine_mi($lat_s, $lon_s, $lat_e, $lon_e);
    if ($mi > 200) {
        return [['level' => 'error', 'field' => 'coords',
                 'message' => sprintf('Put-in to take-out is %.0f mi — too long for a reach.', $mi)]];
    }
    if ($mi > 60) {
        return [['level' => 'warning', 'field' => 'coords',
                 'message' => sprintf('Put-in to take-out is %.0f mi — unusually long.', $mi)]];
    }
    return [];
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

function sanity_errors(array $issues): array {
    return array_values(array_filter($issues, fn($i) => ($i['level'] ?? '') === 'error'));
}

function sanity_warnings(array $issues): array {
    return array_values(array_filter($issues, fn($i) => ($i['level'] ?? '') === 'warning'));
}
