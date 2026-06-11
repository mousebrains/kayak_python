<?php
declare(strict_types=1);
/**
 * Shared state-reference helpers.
 *
 * The dataset owns the state rows; presentation code should derive labels from
 * the synced DB instead of carrying an engine allowlist.
 */

require_once __DIR__ . '/db.php';

/**
 * Return state display names keyed by two-letter abbreviation.
 *
 * @return array<string, string>
 */
function state_names_by_abbreviation(PDO $db): array
{
    $rows = db_rows(db_query(
        $db,
        'SELECT abbreviation, name
         FROM state
         WHERE abbreviation IS NOT NULL
           AND name IS NOT NULL
         ORDER BY name, abbreviation'
    ));

    $states = [];
    foreach ($rows as $row) {
        $raw_abbreviation = $row['abbreviation'] ?? null;
        $raw_name = $row['name'] ?? null;
        if (!is_string($raw_abbreviation) || !is_string($raw_name)) {
            continue;
        }
        $abbreviation = trim($raw_abbreviation);
        $name = trim($raw_name);
        if ($abbreviation !== '' && $name !== '') {
            $states[$abbreviation] = $name;
        }
    }
    return $states;
}
