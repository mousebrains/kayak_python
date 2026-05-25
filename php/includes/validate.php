<?php
declare(strict_types=1);
/**
 * Input validation helpers shared across PHP pages.
 */

/**
 * Validate a date string is in YYYY-MM-DD format and represents a real date.
 * Returns the validated date string, or null if invalid. Accepts the
 * string|false|null that filter_input() yields (false → treated as invalid).
 */
function validate_date(string|false|null $input): ?string {
    if ($input === null || $input === false || $input === '') return null;
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $input)) return null;
    $parts = explode('-', $input);
    if (!checkdate((int)$parts[1], (int)$parts[2], (int)$parts[0])) return null;
    return $input;
}

/**
 * strtotime() for an already-validated date string, narrowing its int|false
 * return. Falls back to the current time if somehow unparseable — defensive;
 * callers pass validate_date() output or a date('Y-m-d') default.
 */
function date_ts(string $date): int {
    $ts = strtotime($date);
    return $ts !== false ? $ts : time();
}
