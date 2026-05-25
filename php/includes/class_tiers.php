<?php
declare(strict_types=1);
/**
 * Parse a whitewater class string into its base tier list.
 *
 * Mirrors src/kayak/utils/class_tiers.py (parse_class_tiers). Cruxes in
 * parentheses are dropped; '+' modifiers are stripped; ranges expand
 * inclusively. Return is sorted Roman-order ['I','II','III','IV','V'].
 *
 * @return list<string>
 */

function parse_class_tiers(?string $s): array {
    if ($s === null || $s === '') return [];
    $cleaned = str_replace('+', '', (string)preg_replace('/\([^)]*\)/', '', $s));
    $roman   = ['I' => 1, 'II' => 2, 'III' => 3, 'IV' => 4, 'V' => 5];
    $found   = [];
    if (preg_match_all(
            '/\b(V|IV|III|II|I)(?:\s*[-\x{2013}]\s*(V|IV|III|II|I))?\b/u',
            $cleaned, $matches, PREG_SET_ORDER | PREG_UNMATCHED_AS_NULL)) {
        foreach ($matches as $hit) {
            $lo = $roman[$hit[1]];
            // With PREG_UNMATCHED_AS_NULL the optional group is null when
            // the range half didn't match (instead of '' under the default
            // flag), so isset() is the only guard we need.
            $hi = isset($hit[2]) ? $roman[$hit[2]] : $lo;
            if ($hi < $lo) [$lo, $hi] = [$hi, $lo];
            for ($v = $lo; $v <= $hi; $v++) $found[$v] = true;
        }
    }
    $rev = array_flip($roman);
    ksort($found);
    return array_map(fn($v) => $rev[$v], array_keys($found));
}
