<?php
/**
 * Input validation helpers shared across PHP pages.
 */

/**
 * Validate a date string is in YYYY-MM-DD format and represents a real date.
 * Returns the validated date string, or null if invalid.
 */
function validate_date(?string $input): ?string {
    if ($input === null || $input === '') return null;
    if (!preg_match('/^\d{4}-\d{2}-\d{2}$/', $input)) return null;
    $parts = explode('-', $input);
    if (!checkdate((int)$parts[1], (int)$parts[2], (int)$parts[0])) return null;
    return $input;
}
