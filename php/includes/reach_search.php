<?php
declare(strict_types=1);
/**
 * Search and state-filter mode for /reach.php.
 *
 * Called from reach.php when ?q= or ?st= is set (after trimming). A
 * single matching reach auto-redirects to /reach.php?id=<single>;
 * otherwise renders a results table + Leaflet map and exits.
 *
 * Convention matches the other helpers in this directory: function-only
 * (no top-level side effects beyond require_once), snake_case names,
 * strict types, helpers prefixed with `_` are file-private.
 */

require_once __DIR__ . '/header.php';
require_once __DIR__ . '/footer.php';
require_once __DIR__ . '/gauge_map.php';

/**
 * Map-marker color palette. Indexed by result row position; mirrors the
 * palette baked into /static/search-map.js so swatches in the results
 * table match map markers. Don't change one without changing the other.
 */
const REACH_SEARCH_MAP_COLORS = [
    '#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4',
    '#42d4f4', '#f032e6', '#bfef45', '#469990', '#dcbeff',
    '#9A6324', '#800000', '#aaffc3', '#808000', '#000075',
];

/**
 * Dispatch search/state-filter mode and write the full HTTP response.
 *
 * Single match → 302 to detail page. No match → "No reaches matching..."
 * empty-state. Multi-match → table + map.
 *
 * @return never  Always exits — via redirect, or footer + exit.
 */
function handle_search_mode(
    PDO $db,
    string $q,
    string $st,
    int $hidden,
    string $compact_css,
): never {
    $results = _search_reaches_query($db, $q, $st, $hidden);

    if (count($results) === 1) {
        header('Location: /reach.php?id=' . $results[0]['id']);
        exit;
    }

    [$reach_readings, $gauge_ids] = _aggregate_reach_readings($db, $results);
    [$reach_classes, $reach_guides] = _aggregate_reach_classes_and_guides($db, $results);

    header('Cache-Control: no-cache');
    $preconnects = '<link rel="preconnect" href="https://a.tile.opentopomap.org">'
        . '<link rel="preconnect" href="https://b.tile.opentopomap.org">'
        . '<link rel="preconnect" href="https://c.tile.opentopomap.org">';
    include_header(
        'Reach Search',
        '',
        '',
        $preconnects . gm_head_links() . $compact_css,
    );
    echo '<h2>Reach Search</h2>';

    $has_map = false;
    $map_scripts = '';
    if (!$results) {
        $label = $q !== '' ? '&ldquo;' . htmlspecialchars($q) . '&rdquo;' : htmlspecialchars($st);
        echo '<p>No reaches matching ' . $label . '.</p>';
    } else {
        _render_search_results_table($results, $q, $st, $reach_readings, $reach_classes, $reach_guides);
        [$has_map, $map_scripts] = _render_search_map($db, $results, $gauge_ids, $reach_readings);
    }

    echo '<p style="margin-top:1rem"><a href="/reach.php">Browse all reaches</a></p>';
    if ($has_map) {
        echo $map_scripts;
    }
    include_footer();
    exit;
}

/**
 * One of three query variants depending on which params are set:
 *   q + st → text search restricted to a state (200 cap)
 *   q only → text search across all states (200 cap)
 *   st only → list every visible reach in the state (1000 cap — Oregon /
 *             California legitimately exceed 200)
 *
 * @return list<array<string, mixed>>
 */
function _search_reaches_query(PDO $db, string $q, string $st, int $hidden): array
{
    $cols = 'r.id, COALESCE(NULLIF(r.display_name, \'\'), r.name) AS name, r.river,'
        . ' r.description, r.gauge_id, r.latitude_start, r.longitude_start,'
        . ' r.latitude_end, r.longitude_end, r.latitude, r.longitude,'
        . ' r.sort_name, r.aw_id, r.geom';

    if ($q !== '' && $st !== '') {
        $pat = "%$q%";
        $stmt = $db->prepare(
            "SELECT $cols
             FROM reach r
             JOIN reach_state rs ON rs.reach_id = r.id
             JOIN state s ON s.id = rs.state_id
             WHERE (r.display_name LIKE ? OR r.name LIKE ? OR r.river LIKE ?)
               AND s.abbreviation = ?
               AND r.no_show = ?
             ORDER BY r.sort_name
             LIMIT 200"
        );
        $stmt->execute([$pat, $pat, $pat, $st, $hidden]);
    } elseif ($q !== '') {
        $pat = "%$q%";
        $stmt = $db->prepare(
            "SELECT $cols
             FROM reach r
             WHERE (r.display_name LIKE ? OR r.name LIKE ? OR r.river LIKE ?)
               AND r.no_show = ?
             ORDER BY r.sort_name
             LIMIT 200"
        );
        $stmt->execute([$pat, $pat, $pat, $hidden]);
    } else {
        $stmt = $db->prepare(
            "SELECT $cols
             FROM reach r
             JOIN reach_state rs ON rs.reach_id = r.id
             JOIN state s ON s.id = rs.state_id
             WHERE s.abbreviation = ?
               AND r.no_show = ?
             ORDER BY r.sort_name
             LIMIT 1000"
        );
        $stmt->execute([$st, $hidden]);
    }
    return $stmt->fetchAll();
}

/**
 * Roll up the latest flow/gauge/inflow reading per gauge for the matched
 * reaches. Within a gauge: flow wins over inflow (inflow only used if no
 * flow row exists); gauge is independent.
 *
 * Also returns the list of unique non-null gauge_ids — the map renderer
 * reuses it for the gauges-with-locations query.
 *
 * @param  list<array<string, mixed>>                        $results
 * @return array{0: array<int, array<string, array<string, mixed>>>, 1: list<int>}
 */
function _aggregate_reach_readings(PDO $db, array $results): array
{
    $reach_readings = [];
    if (!$results) {
        return [$reach_readings, []];
    }
    $gauge_ids = array_values(array_unique(array_filter(array_column($results, 'gauge_id'))));
    if (!$gauge_ids) {
        return [$reach_readings, []];
    }

    $placeholders = implode(',', array_fill(0, count($gauge_ids), '?'));
    $lo_stmt = $db->prepare(
        "SELECT gs.gauge_id, lo.data_type, lo.value, lo.observed_at
         FROM latest_observation lo
         JOIN gauge_source gs ON gs.source_id = lo.source_id
         WHERE gs.gauge_id IN ($placeholders)
           AND lo.data_type IN ('flow', 'gauge', 'inflow')
         ORDER BY gs.gauge_id, lo.data_type"
    );
    $lo_stmt->execute($gauge_ids);
    foreach ($lo_stmt->fetchAll() as $lo) {
        $gid = $lo['gauge_id'];
        $dt = $lo['data_type'];
        if (!isset($reach_readings[$gid][$dt])
            || ($dt === 'flow' || ($dt === 'inflow' && !isset($reach_readings[$gid]['flow'])))) {
            $reach_readings[$gid][$dt] = $lo;
        }
    }
    return [$reach_readings, $gauge_ids];
}

/**
 * Roll up classes (Class III, IV, ...) and guidebook abbreviations
 * (SS5, AW, ...) per reach. Soggy Sneakers editions collapse to a
 * single SS<editions> label; non-SS guides keep two-letter abbrevs.
 * Reaches with aw_id but no guidebook row still get an "AW" entry.
 *
 * @param  list<array<string, mixed>>                                       $results
 * @return array{0: array<int, list<string>>, 1: array<int, array<string, true>>}
 */
function _aggregate_reach_classes_and_guides(PDO $db, array $results): array
{
    $reach_classes = [];
    $reach_guides = [];
    $reach_ids = array_column($results, 'id');
    if (!$reach_ids) {
        return [$reach_classes, $reach_guides];
    }

    $ph = implode(',', array_fill(0, count($reach_ids), '?'));

    $cls_stmt = $db->prepare("SELECT reach_id, name FROM reach_class WHERE reach_id IN ($ph)");
    $cls_stmt->execute($reach_ids);
    foreach ($cls_stmt->fetchAll() as $c) {
        $reach_classes[$c['reach_id']][] = $c['name'];
    }

    $gb_stmt = $db->prepare(
        "SELECT rg.reach_id, g.id AS gid, g.title
         FROM reach_guidebook rg
         JOIN guidebook g ON g.id = rg.guidebook_id
         WHERE rg.reach_id IN ($ph)
         ORDER BY g.sort_order, g.title, g.edition"
    );
    $gb_stmt->execute($reach_ids);
    // Soggy Sneakers edition number by guidebook id.
    $ss_edition = [9 => 1, 2 => 3, 3 => 4, 4 => 5];
    // Non-SS guidebook abbreviation map.
    $gb_abbrev = [
        5 => 'ID',    // Idaho
        6 => 'WA',    // Guide to WW Rivers of Washington
        7 => 'PO',    // Paddling Oregon
        8 => 'AW',    // American Whitewater
        10 => 'OK',   // Oregon Kayaking
        11 => 'DF',   // Dreamflows
    ];
    $reach_ss = [];  // reach_id => [edition numbers]
    foreach ($gb_stmt->fetchAll() as $gb) {
        $gid = $gb['gid'];
        $rid = $gb['reach_id'];
        if (isset($ss_edition[$gid])) {
            $reach_ss[$rid][] = $ss_edition[$gid];
        } else {
            $abbr = $gb_abbrev[$gid] ?? substr($gb['title'], 0, 2);
            $reach_guides[$rid][$abbr] = true;
        }
    }
    // Build "SS531" style labels from collected editions (newest first)
    // and prepend so SS appears before non-SS guides.
    foreach ($reach_ss as $rid => $editions) {
        rsort($editions);
        $ss_label = 'SS' . implode('', $editions);
        $reach_guides[$rid] = [$ss_label => true] + ($reach_guides[$rid] ?? []);
    }

    foreach ($results as $r) {
        if (!empty($r['aw_id'])) {
            $reach_guides[$r['id']]['AW'] = true;
        }
    }

    return [$reach_classes, $reach_guides];
}

/**
 * Render the matched-reaches table — one row per result.
 *
 * @param list<array<string, mixed>>                            $results
 * @param array<int, array<string, array<string, mixed>>>       $reach_readings
 * @param array<int, list<string>>                              $reach_classes
 * @param array<int, array<string, true>>                       $reach_guides
 */
function _render_search_results_table(
    array $results,
    string $q,
    string $st,
    array $reach_readings,
    array $reach_classes,
    array $reach_guides,
): void {
    $label = $q !== '' ? '&ldquo;' . htmlspecialchars($q) . '&rdquo;' : htmlspecialchars($st);
    echo '<p>' . count($results) . ' reaches matching ' . $label . ':</p>';
    echo '<table class="desc-table">';
    echo '<tr><th>ID</th><th>Name</th><th>Description</th><th>Class</th>'
        . '<th>Sort Name</th><th>Guides</th><th>Flow / Gage</th></tr>';
    foreach ($results as $idx => $r) {
        $rname = htmlspecialchars($r['name']);
        $desc = htmlspecialchars($r['description'] ?? '');
        $sname = htmlspecialchars($r['sort_name'] ?? '');
        $reading = '';
        if ($r['gauge_id'] && isset($reach_readings[$r['gauge_id']])) {
            $rr = $reach_readings[$r['gauge_id']];
            $parts = [];
            if (isset($rr['flow'])) {
                $parts[] = number_format((float)$rr['flow']['value'], 0) . ' cfs';
            } elseif (isset($rr['inflow'])) {
                $parts[] = number_format((float)$rr['inflow']['value'], 0) . ' cfs';
            }
            if (isset($rr['gauge'])) {
                $parts[] = number_format((float)$rr['gauge']['value'], 2) . ' ft';
            }
            $reading = implode(' / ', $parts);
        }
        $color = REACH_SEARCH_MAP_COLORS[$idx % count(REACH_SEARCH_MAP_COLORS)];
        $swatch = '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:'
            . $color . ';margin-right:4px" title="Map marker color"></span>';
        $cls = htmlspecialchars(implode(', ', $reach_classes[$r['id']] ?? []));
        $guides = implode(', ', array_keys($reach_guides[$r['id']] ?? []));
        echo "<tr><td>{$r['id']}</td><td>{$swatch}<a href=\"/reach.php?id={$r['id']}\">$rname</a></td>"
            . "<td>$desc</td><td>$cls</td><td>$sname</td><td>$guides</td><td>$reading</td></tr>\n";
    }
    echo '</table>';
}

/**
 * Build the result-set's map payload (reaches with coords + optional
 * downsampled track polylines, plus unique gauges with locations) and
 * emit the <div id="search-map"> element. The Leaflet <script> tags
 * are returned to the caller so they can be deferred until after the
 * footer.
 *
 * @param  list<array<string, mixed>>                       $results
 * @param  list<int>                                        $gauge_ids
 * @param  array<int, array<string, array<string, mixed>>>  $reach_readings
 * @return array{0: bool, 1: string}  [has_map, deferred_script_tags]
 */
function _render_search_map(PDO $db, array $results, array $gauge_ids, array $reach_readings): array
{
    $map_reaches = _build_search_map_reaches($results);
    if (!$map_reaches) {
        return [false, ''];
    }

    $map_gauges = _collect_search_map_gauges($db, $gauge_ids, $reach_readings);

    $map_json = htmlspecialchars(json_encode($map_reaches));
    $colors_json = htmlspecialchars(json_encode(REACH_SEARCH_MAP_COLORS));
    $gauges_json = htmlspecialchars(json_encode($map_gauges));
    echo '<div id="search-map" style="height:65vh;min-height:480px;margin-top:1rem;border:1px solid #ccc"'
        . ' data-reaches="' . $map_json . '" data-colors="' . $colors_json
        . '" data-gauges="' . $gauges_json . '"></div>';

    return [
        true,
        '<script src="/static/leaflet.js" defer></script>'
        . '<script src="/static/search-map.js" defer></script>',
    ];
}

/**
 * Lat/lon + (optionally downsampled to ~100 pts) track polyline for the
 * result reaches with map-renderable coords. Skips reaches with no lat/lon.
 *
 * @param  list<array<string, mixed>> $results
 * @return list<array<string, mixed>>
 */
function _build_search_map_reaches(array $results): array
{
    $map_reaches = [];
    foreach ($results as $idx => $r) {
        $lat = $r['latitude'] ?? $r['latitude_start'] ?? null;
        $lon = $r['longitude'] ?? $r['longitude_start'] ?? null;
        if ($lat === null || $lon === null) {
            continue;
        }
        $track = null;
        if (!empty($r['geom'])) {
            $track = [];
            foreach (explode(',', $r['geom']) as $pair) {
                $parts = preg_split('/\s+/', trim($pair));
                if (count($parts) === 2) {
                    $track[] = [(float)$parts[1], (float)$parts[0]];
                }
            }
            // Downsample to ~100 points for search map.
            $n = count($track);
            if ($n > 100) {
                $step = $n / 100;
                $sampled = [];
                for ($i = 0; $i < 100; $i++) {
                    $sampled[] = $track[(int)($i * $step)];
                }
                $sampled[] = $track[$n - 1];
                $track = $sampled;
            }
        }
        $map_reaches[] = [
            'id' => $r['id'],
            'name' => $r['name'],
            'lat' => (float)$lat,
            'lon' => (float)$lon,
            'lat_start' => $r['latitude_start'] ? (float)$r['latitude_start'] : null,
            'lon_start' => $r['longitude_start'] ? (float)$r['longitude_start'] : null,
            'lat_end' => $r['latitude_end'] ? (float)$r['latitude_end'] : null,
            'lon_end' => $r['longitude_end'] ? (float)$r['longitude_end'] : null,
            'track' => $track,
            'idx' => $idx,
        ];
    }
    return $map_reaches;
}

/**
 * Gauge name + lat/lon + optional flow/gage label, for the result-set's
 * unique gauges that have coords. Labels mirror the table's reading column.
 *
 * @param  list<int>                                        $gauge_ids
 * @param  array<int, array<string, array<string, mixed>>>  $reach_readings
 * @return list<array<string, mixed>>
 */
function _collect_search_map_gauges(PDO $db, array $gauge_ids, array $reach_readings): array
{
    if (!$gauge_ids) {
        return [];
    }
    $ph = implode(',', array_fill(0, count($gauge_ids), '?'));
    $g_stmt = $db->prepare(
        "SELECT id, name, latitude, longitude FROM gauge
         WHERE id IN ($ph) AND latitude IS NOT NULL AND longitude IS NOT NULL"
    );
    $g_stmt->execute($gauge_ids);

    $map_gauges = [];
    foreach ($g_stmt->fetchAll() as $g) {
        $glabel = $g['name'];
        if (isset($reach_readings[$g['id']])) {
            $parts = [];
            $rr = $reach_readings[$g['id']];
            if (isset($rr['flow'])) {
                $parts[] = number_format((float)$rr['flow']['value'], 0) . ' cfs';
            }
            if (isset($rr['gauge'])) {
                $parts[] = number_format((float)$rr['gauge']['value'], 2) . ' ft';
            }
            if ($parts) {
                $glabel .= ' (' . implode(' / ', $parts) . ')';
            }
        }
        $map_gauges[] = [
            'name' => $glabel,
            'lat' => (float)$g['latitude'],
            'lon' => (float)$g['longitude'],
        ];
    }
    return $map_gauges;
}
