<?php

declare(strict_types=1);

/**
 * Search mode for /gauge.php?q=<term>.
 *
 * Called from gauge.php when ?q= is set (after trim). A single matching
 * gauge auto-redirects to /gauge.php?id=<single>; otherwise renders the
 * matched-gauges table and exits.
 *
 * Convention matches the other helpers in this directory: function-only
 * (no top-level side effects beyond require_once), snake_case names,
 * strict types, helpers prefixed with `_` are file-private.
 */

require_once __DIR__ . '/header.php';
require_once __DIR__ . '/footer.php';
require_once __DIR__ . '/http_exit.php';

/**
 * Dispatch search mode and write the full HTTP response.
 *
 * Single match → 302 to detail page. No match → "No gauges matching..."
 * empty-state. Multi-match → results table.
 *
 * @return never  Always exits — via redirect, or footer + exit.
 */
function handle_gauge_search(PDO $db, string $q): never
{
    $results = _search_gauges($db, $q);

    if (count($results) === 1) {
        header('Location: /gauge.php?id=' . $results[0]['id']);
        http_terminate(302);
    }

    header('Cache-Control: no-cache');
    include_header('Gauge Search', '', '', '', ['picker_kind' => 'gauge']);
    echo '<h2>Gauge Search</h2>';

    if ($results === []) {
        echo '<p>No gauges matching &ldquo;' . htmlspecialchars($q) . '&rdquo;.</p>';
    } else {
        _render_gauge_search_results($results, $q);
    }

    echo '<p style="margin-top:1rem"><a href="/gauge.php">Browse all gauges</a></p>';
    include_footer();
    http_terminate(200);
}

/**
 * One LIKE-anywhere query across the searchable text columns. Selects
 * `name` (not `display_name`) because the canonical name is what users
 * type when grepping for a gauge id token (e.g. USGS station number).
 *
 * @return list<array<string, mixed>>
 */
function _search_gauges(PDO $db, string $q): array
{
    $stmt = $db->prepare(
        'SELECT id, name, location FROM gauge
         WHERE name LIKE ? OR location LIKE ? OR station_id LIKE ?
            OR usgs_id LIKE ? OR cbtt_id LIKE ? OR geos_id LIKE ?
            OR nws_id LIKE ? OR nwsli_id LIKE ? OR snotel_id LIKE ?
         ORDER BY id
         LIMIT 200'
    );
    $pat = "%$q%";
    $stmt->execute([$pat, $pat, $pat, $pat, $pat, $pat, $pat, $pat, $pat]);
    return db_rows($stmt);
}

/**
 * Match-count header + 3-col results table. Each row links to
 * /gauge.php?id=N. Renders canonical `name` (not display_name) — see
 * `_search_gauges` for rationale.
 *
 * @param list<array<string, mixed>> $results
 */
function _render_gauge_search_results(array $results, string $q): void
{
    echo '<p>' . count($results) . ' gauges matching &ldquo;' . htmlspecialchars($q) . '&rdquo;:</p>';
    echo '<table class="desc-table">';
    echo '<tr><th>ID</th><th>Name</th><th>Location</th></tr>';
    foreach ($results as $r) {
        $name = htmlspecialchars($r['name']);
        $loc = htmlspecialchars($r['location'] ?? '');
        echo "<tr><td>{$r['id']}</td><td><a href=\"/gauge.php?id={$r['id']}\">$name</a></td><td>$loc</td></tr>\n";
    }
    echo '</table>';
}
