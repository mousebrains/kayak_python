<?php
declare(strict_types=1);
/**
 * Detail page rendering for /description.php — readings, plots, map,
 * metadata.
 *
 * Single-mode entry point (no search/list dispatch — every request is
 * a detail render for a given reach id). Loads the reach + navigation
 * context + related data (gauge, states, classes, flow levels, readings,
 * guidebooks, data sources) and renders the full HTML response.
 *
 * Convention matches the other helpers in this directory: function-only
 * (no top-level side effects beyond require_once), snake_case names,
 * strict types, helpers prefixed with `_` are file-private.
 */

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/header.php';
require_once __DIR__ . '/pubhash_request.php';
require_once __DIR__ . '/footer.php';
require_once __DIR__ . '/html.php';
require_once __DIR__ . '/svg_plot.php';
require_once __DIR__ . '/gauge_plots.php';
require_once __DIR__ . '/gauge_map.php';
require_once __DIR__ . '/reach_fields.php';

/**
 * Dispatch detail mode and write the full HTTP response.
 *
 * 404s with the rich HTML page from `get_reach_or_404` if the id has
 * no reach row.
 */
function handle_description_detail(
    PDO $db,
    int $id,
    ?string $start_date,
    ?string $end_date,
    int $hidden,
): void {
    /** @var array{id: int, updated_at: string|null, gauge_id: int|null, name: string|null, display_name: string|null, sort_name: string|null, nature: string|null, description: string|null, difficulties: string|null, basin: string|null, basin_area: float|null, elevation: float|null, elevation_lost: float|null, length: float|null, gradient: float|null, features: string|null, latitude: float|null, longitude: float|null, latitude_start: float|null, longitude_start: float|null, latitude_end: float|null, longitude_end: float|null, no_show: int, notes: string|null, optimal_flow: float|null, region: string|null, remoteness: string|null, scenery: string|null, season: string|null, watershed_type: string|null, aw_id: int|null, river: string|null, max_gradient: float|null, geom: string|null, huc: string|null, map_only: int, no_flow_range: int, gradient_profile: string|null, gradient_unreliable: int} $reach */
    $reach = get_reach_or_404($id);
    $name = ($reach['display_name'] !== null && $reach['display_name'] !== '')
        ? $reach['display_name']
        : ($reach['name'] ?? '');
    $nav = _load_description_navigation($db, $reach, $id, $hidden);
    $related = _load_description_related($db, $reach, $id);
    $readings = _load_current_readings($db, $related['gauge']);

    _render_description_header($id, $name);
    _render_description_nav_bar(
        $id,
        $hidden,
        $nav['prev'],
        $nav['next'],
        $nav['position'],
        $nav['total'],
    );
    $h2 = htmlspecialchars($name);
    $location = trim($reach['description'] ?? '');
    if ($location !== '') {
        $h2 .= ' -- ' . htmlspecialchars($location);
    }
    echo '<h2>' . $h2 . '</h2>';

    _render_current_readings($readings);

    if ($related['gauge'] !== null) {
        _render_description_date_form_and_plots(
            $db,
            $related['gauge'],
            $id,
            $name,
            $start_date,
            $end_date,
            $related['class_range'],
        );
    }

    $has_map = _render_description_fields_and_map($reach, $related, $readings);
    _render_data_sources($db, $related['gauge']);
    _render_description_guidebooks($db, $reach, $id);
    _render_description_footer($id);

    $doc_root = is_string($_SERVER['DOCUMENT_ROOT'] ?? null) ? $_SERVER['DOCUMENT_ROOT'] : '';
    if ($has_map) {
        $fm_raw = @filemtime($doc_root . '/static/feature-map.js');
        $fm_mtime = $fm_raw !== false ? $fm_raw : 1;
        echo '<script src="/static/leaflet.js" defer></script>';
        echo '<script src="/static/feature-map.js?v=' . $fm_mtime . '" defer></script>';
    }
    // gradient-profile.js is independent of the map (degrades to chart-only
    // tooltip if the map isn't there), so we ship it regardless of $has_map.
    // It's a no-op when the page has no .gradient-profile-chart elements.
    $gp_raw = @filemtime($doc_root . '/static/gradient-profile.js');
    $gp_mtime = $gp_raw !== false ? $gp_raw : 1;
    echo '<script src="/static/gradient-profile.js?v=' . $gp_mtime . '" defer></script>';
    include_footer();
}

/**
 * Prev/next reach ids by sort_name, plus total + current position.
 * Same 4-query shape as reach_detail's `_load_reach_navigation` (one
 * of three sub-clusters that overlap; see docs/done/PLAN_php_layer_split.md Tier 3
 * follow-up note about a future shared-helpers DRY pass).
 *
 * @param  array{sort_name: string|null} $reach
 * @return array{
 *     prev: array{id: int}|false,
 *     next: array{id: int}|false,
 *     position: int,
 *     total: int
 * }
 */
function _load_description_navigation(PDO $db, array $reach, int $id, int $hidden): array
{
    $prev_stmt = $db->prepare(
        'SELECT id FROM reach WHERE (sort_name < ? OR (sort_name = ? AND id < ?))
         AND no_show = ? ORDER BY sort_name DESC, id DESC LIMIT 1'
    );
    $prev_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
    /** @var array{id: int}|false $prev */
    $prev = db_row($prev_stmt);

    $next_stmt = $db->prepare(
        'SELECT id FROM reach WHERE (sort_name > ? OR (sort_name = ? AND id > ?))
         AND no_show = ? ORDER BY sort_name ASC, id ASC LIMIT 1'
    );
    $next_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
    /** @var array{id: int}|false $next */
    $next = db_row($next_stmt);

    $total_stmt = $db->prepare('SELECT COUNT(*) FROM reach WHERE no_show = ?');
    $total_stmt->execute([$hidden]);
    $total = (int)$total_stmt->fetchColumn();

    $pos_stmt = $db->prepare(
        'SELECT COUNT(*) FROM reach WHERE (sort_name < ? OR (sort_name = ? AND id <= ?))
         AND no_show = ?'
    );
    $pos_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
    $position = (int)$pos_stmt->fetchColumn();

    return ['prev' => $prev, 'next' => $next, 'position' => $position, 'total' => $total];
}

/**
 * Gauge (or null), states, classes (name-only), the raw class_range row
 * (needed by `gp_render_plots`), and the derived flow_levels for the
 * fields-table render.
 *
 * @param  array{gauge_id: int|null} $reach
 * @return array{
 *     gauge: array{id: int, name: string, bank_full: float|null, flood_stage: float|null, location: string|null, latitude: float|null, longitude: float|null, station_id: string|null, cbtt_id: string|null, geos_id: string|null, nws_id: string|null, nwsli_id: string|null, snotel_id: string|null, usgs_id: string|null, rating_id: int|null, elevation: float|null, drainage_area: float|null, huc: string|null, allow_negative_flow: int, river: string|null, display_name: string|null, sort_name: string|null, state: string|null}|null,
 *     states: list<string>,
 *     classes: list<string>,
 *     class_range: array{low: float|null, low_data_type: string|null, high: float|null, high_data_type: string|null}|false,
 *     flow_levels: list<array{level: string, low: float|null, low_data_type: string, high: float|null, high_data_type: string}>
 * }
 */
function _load_description_related(PDO $db, array $reach, int $id): array
{
    $gauge = null;
    if ($reach['gauge_id'] !== null) {
        $stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
        $stmt->execute([$reach['gauge_id']]);
        /** @var array{id: int, name: string, bank_full: float|null, flood_stage: float|null, location: string|null, latitude: float|null, longitude: float|null, station_id: string|null, cbtt_id: string|null, geos_id: string|null, nws_id: string|null, nwsli_id: string|null, snotel_id: string|null, usgs_id: string|null, rating_id: int|null, elevation: float|null, drainage_area: float|null, huc: string|null, allow_negative_flow: int, river: string|null, display_name: string|null, sort_name: string|null, state: string|null}|false $g */
        $g = $stmt->fetch();
        $gauge = $g === false ? null : $g;
    }

    $states_stmt = $db->prepare(
        'SELECT s.name FROM state s JOIN reach_state rs ON s.id = rs.state_id WHERE rs.reach_id = ?'
    );
    $states_stmt->execute([$id]);
    $states = array_column($states_stmt->fetchAll(), 'name');

    $classes_stmt = $db->prepare('SELECT name FROM reach_class WHERE reach_id = ?');
    $classes_stmt->execute([$id]);
    $classes = array_column($classes_stmt->fetchAll(), 'name');

    $class_range_stmt = $db->prepare(
        'SELECT low, low_data_type, high, high_data_type
         FROM reach_class
         WHERE reach_id = ? AND (low IS NOT NULL OR high IS NOT NULL)
         ORDER BY id LIMIT 1'
    );
    $class_range_stmt->execute([$id]);
    /** @var array{low: float|null, low_data_type: string|null, high: float|null, high_data_type: string|null}|false $class_range */
    $class_range = $class_range_stmt->fetch();
    $flow_levels = _derive_description_flow_levels($class_range);

    return [
        'gauge' => $gauge,
        'states' => $states,
        'classes' => $classes,
        'class_range' => $class_range,
        'flow_levels' => $flow_levels,
    ];
}

/**
 * Derive low/okay/high flow-level bands from a class_range row (same logic
 * as reach_detail._derive_reach_flow_levels but takes the row directly
 * rather than re-querying — caller in _load_description_related already
 * fetched it because gp_render_plots needs the raw row too).
 *
 * @param  array{low: float|null, low_data_type: string|null, high: float|null, high_data_type: string|null}|false $class_range
 * @return list<array{level: string, low: float|null, low_data_type: string, high: float|null, high_data_type: string}>
 */
function _derive_description_flow_levels(array|false $class_range): array
{
    if ($class_range === false) {
        return [];
    }
    $lo = $class_range['low'];
    $hi = $class_range['high'];
    $lo_dt = ($class_range['low_data_type'] !== null && $class_range['low_data_type'] !== '')
        ? $class_range['low_data_type'] : 'flow';
    $hi_dt = ($class_range['high_data_type'] !== null && $class_range['high_data_type'] !== '')
        ? $class_range['high_data_type'] : 'flow';
    return [
        ['level' => 'low',  'low' => null, 'low_data_type' => $lo_dt, 'high' => $lo,   'high_data_type' => $lo_dt],
        ['level' => 'okay', 'low' => $lo,  'low_data_type' => $lo_dt, 'high' => $hi,   'high_data_type' => $hi_dt],
        ['level' => 'high', 'low' => $hi,  'low_data_type' => $hi_dt, 'high' => null,  'high_data_type' => $hi_dt],
    ];
}

/**
 * Latest gauge observations for the readings table. Returns empty list
 * when the reach has no linked gauge (caller's render also skips in
 * that case).
 *
 * @param  array{id: int}|null $gauge
 * @return list<array{data_type: string, value: float, observed_at: string, delta_per_hour: float|null}>
 */
function _load_current_readings(PDO $db, ?array $gauge): array
{
    if ($gauge === null) {
        return [];
    }
    $stmt = $db->prepare(
        'SELECT data_type, value, observed_at, delta_per_hour
         FROM latest_gauge_observation WHERE gauge_id = ?'
    );
    $stmt->execute([$gauge['id']]);
    /** @var list<array{data_type: string, value: float, observed_at: string, delta_per_hour: float|null}> $rows */
    $rows = db_rows($stmt);
    return $rows;
}

/**
 * `Cache-Control: private` (response embeds the editor's email in the
 * nav, so intermediary proxies must not cache it). Tile-server
 * preconnects + `include_header` with editor-feature context for the
 * "Comment" nav link's redirect.
 */
function _render_description_header(int $id, string $name): void
{
    header('Cache-Control: private, max-age=300');
    $preconnects = '<link rel="preconnect" href="https://a.tile.opentopomap.org">'
        . '<link rel="preconnect" href="https://b.tile.opentopomap.org">'
        . '<link rel="preconnect" href="https://c.tile.opentopomap.org">';
    include_header(
        "$name - Description",
        '',
        "Real-time river data for $name — flow, gage height, and conditions.",
        $preconnects . gm_head_links(),
        ['type' => 'reach', 'id' => $id]
    );
}

/**
 * Prev/next nav bar at the top of the body. Simpler than reach_detail's
 * — no embedded search form, no state-select, no hidden toggle (this is
 * a description page, not the reach picker).
 *
 * @param array{id: int}|false $prev
 * @param array{id: int}|false $next
 */
function _render_description_nav_bar(
    int $id,
    int $hidden,
    array|false $prev,
    array|false $next,
    int $position,
    int $total,
): void {
    echo '<nav aria-label="Reach navigation" style="display:flex;align-items:center;'
        . 'gap:1rem;margin-bottom:.5rem;flex-wrap:wrap">';
    $hq = $hidden !== 0 ? '&amp;hidden=1' : '';
    if ($prev !== false) {
        echo '<a href="' . pubhash_url('description', $prev['id'], $hq) . '">&laquo; Prev</a>';
    } else {
        echo '<span style="color:#999">&laquo; Prev</span>';
    }
    echo "<span>Reach $position of $total</span>";
    if ($next !== false) {
        echo '<a href="' . pubhash_url('description', $next['id'], $hq) . '">Next &raquo;</a>';
    } else {
        echo '<span style="color:#999">Next &raquo;</span>';
    }
    echo '</nav>';
}

/**
 * 5-col readings table — Type / Value / Time / Change/hr / Status.
 * Status is a <span> with .stable / .rising / .falling class; threshold
 * is |delta_per_hour| < 0.5 for "stable". Skipped if no readings.
 *
 * @param list<array{data_type: string, value: float, observed_at: string, delta_per_hour: float|null}> $readings
 */
function _render_current_readings(array $readings): void
{
    if ($readings === []) {
        return;
    }
    $type_labels = [
        'flow' => 'Flow',
        'gauge' => 'Gage Height',
        'temperature' => 'Temperature',
        'inflow' => 'Inflow',
    ];
    $type_units = [
        'flow' => 'CFS',
        'gauge' => 'Feet',
        'temperature' => 'F',
        'inflow' => 'CFS',
    ];
    echo '<table class="readings-table">';
    echo '<tr><th>Type</th><th>Value</th><th>Time</th><th>Change/hr</th><th>Status</th></tr>';
    foreach ($readings as $r) {
        $label = $type_labels[$r['data_type']] ?? htmlspecialchars($r['data_type']);
        $unit = $type_units[$r['data_type']] ?? '';
        $raw = $r['value'];
        if ($r['data_type'] === 'flow' || $r['data_type'] === 'inflow') {
            $val = number_format($raw, 0) . " $unit";
        } else {
            $val = number_format($raw, 1) . " $unit";
        }
        $time_iso = gmdate('Y-m-d\TH:i:s\Z', (int)strtotime($r['observed_at']));
        $time_display = date('m/d H:i', (int)strtotime($r['observed_at']));
        $time_html = "<time datetime=\"$time_iso\">$time_display</time>";
        $delta_dec = ($r['data_type'] === 'flow' || $r['data_type'] === 'inflow') ? 0 : 2;
        $delta = $r['delta_per_hour'] !== null ? number_format($r['delta_per_hour'], $delta_dec) : '';
        $status = '';
        if ($r['delta_per_hour'] !== null) {
            $dph = $r['delta_per_hour'];
            if (abs($dph) < 0.5) {
                $status = '<span class="stable">stable</span>';
            } elseif ($dph > 0) {
                $status = '<span class="rising">rising</span>';
            } else {
                $status = '<span class="falling">falling</span>';
            }
        }
        echo "<tr><td>$label</td><td>$val</td><td>$time_html</td><td>$delta</td><td>$status</td></tr>\n";
    }
    echo '</table>';
}

/**
 * Date-window selector + the inline SVG plots — wraps the
 * gp_resolve_window / gp_render_date_form / gp_render_plots trio.
 * Only called when the reach has a linked gauge.
 *
 * @param array{id: int}             $gauge
 * @param array{low: float|null, low_data_type: string|null, high: float|null, high_data_type: string|null}|false $class_range
 */
function _render_description_date_form_and_plots(
    PDO $db,
    array $gauge,
    int $id,
    string $name,
    ?string $start_date,
    ?string $end_date,
    array|false $class_range,
): void {
    [$latest_ts, $since, $until, $is_default_view] =
        gp_resolve_window($db, $gauge['id'], $start_date, $end_date);
    gp_render_date_form(
        $id,
        $start_date,
        $end_date,
        $latest_ts,
        [['label' => 'Data inspector', 'url' => pubhash_url('data', $id)]]
    );
    gp_render_plots(
        $db,
        $gauge['id'],
        $name,
        $since,
        $until,
        $latest_ts,
        $is_default_view,
        $class_range === false ? null : $class_range,
    );
}

/**
 * Description fields table + (if any map points) an inline Leaflet
 * map via `gm_render_map`. Returns the bool gm_render_map returned
 * so the caller knows whether to emit Leaflet `<script>` tags at the
 * end of the page.
 *
 * The fields list and the map both live in this helper because the
 * coordinate fields ('Gauge Location', 'Put-in', 'Take-out') and the
 * `$map_points` array are built together — splitting them would
 * duplicate the lat/lon-to-string formatting.
 *
 * @param  array{id: int, updated_at: string|null, gauge_id: int|null, name: string|null, display_name: string|null, sort_name: string|null, nature: string|null, description: string|null, difficulties: string|null, basin: string|null, basin_area: float|null, elevation: float|null, elevation_lost: float|null, length: float|null, gradient: float|null, features: string|null, latitude: float|null, longitude: float|null, latitude_start: float|null, longitude_start: float|null, latitude_end: float|null, longitude_end: float|null, no_show: int, notes: string|null, optimal_flow: float|null, region: string|null, remoteness: string|null, scenery: string|null, season: string|null, watershed_type: string|null, aw_id: int|null, river: string|null, max_gradient: float|null, geom: string|null, huc: string|null, map_only: int, no_flow_range: int, gradient_profile: string|null, gradient_unreliable: int} $reach
 * @param  array{
 *     gauge: array{id: int, name: string, bank_full: float|null, flood_stage: float|null, location: string|null, latitude: float|null, longitude: float|null, station_id: string|null, cbtt_id: string|null, geos_id: string|null, nws_id: string|null, nwsli_id: string|null, snotel_id: string|null, usgs_id: string|null, rating_id: int|null, elevation: float|null, drainage_area: float|null, huc: string|null, allow_negative_flow: int, river: string|null, display_name: string|null, sort_name: string|null, state: string|null}|null,
 *     states: list<string>,
 *     classes: list<string>,
 *     class_range: array{low: float|null, low_data_type: string|null, high: float|null, high_data_type: string|null}|false,
 *     flow_levels: list<array{level: string, low: float|null, low_data_type: string, high: float|null, high_data_type: string}>
 * } $related
 * @param  list<array{data_type: string, value: float, observed_at: string, delta_per_hour: float|null}> $readings
 */
function _render_description_fields_and_map(array $reach, array $related, array $readings): bool
{
    echo '<table class="desc-table">';

    $gauge = $related['gauge'];
    $gauge_html = null;
    if ($gauge !== null) {
        // Hyperlink the gauge name through to /gauge.php so users can
        // reach the per-gauge readings table, plot, map, associated
        // sources, and (for regression-derived calc gauges) the
        // analysis writeup. Fall back to location text when the gauge
        // has no location set.
        $gauge_label = ($gauge['location'] ?? '') !== ''
            ? $gauge['location']
            : (($gauge['display_name'] ?? '') !== '' ? $gauge['display_name'] : $gauge['name']);
        $gauge_html = '<a href="' . pubhash_url('gauge', $gauge['id']) . '">'
            . htmlspecialchars((string)$gauge_label) . '</a>';
    }
    $fields = [
        'Description' => $reach['description'],
        'Class' => implode(', ', $related['classes']),
        'Watershed' => format_reach_watershed($reach, $related['states']),
        'Gauge' => $gauge_html,
        'Season' => $reach['season'],
        'Length' => format_reach_length($reach),
        'Elevation' => format_reach_elevation($reach),
        'Scenery' => $reach['scenery'],
        'Features' => $reach['features'],
        'Remoteness' => $reach['remoteness'],
        'Nature' => $reach['nature'],
        'Watershed type' => $reach['watershed_type'],
        'Optimal Flow' => (bool)$reach['optimal_flow']
            ? number_format($reach['optimal_flow'], 0) . ' CFS'
            : null,
        'Flow' => format_reach_flow($related['flow_levels']),
    ];

    $map_points = [];
    $coord_fields = [];
    if ($gauge !== null && $gauge['latitude'] !== null && $gauge['longitude'] !== null) {
        $glat = number_format($gauge['latitude'], 5, '.', '');
        $glon = number_format($gauge['longitude'], 5, '.', '');
        $coord_fields['Gauge Location'] = [$glat, $glon];
        $map_points['Gauge'] = "$glat,$glon";
    }
    if ($reach['latitude_start'] !== null && $reach['longitude_start'] !== null) {
        $slat = number_format($reach['latitude_start'], 5, '.', '');
        $slon = number_format($reach['longitude_start'], 5, '.', '');
        $coord_fields['Put-in'] = [$slat, $slon];
        $map_points['Put-in'] = "$slat,$slon";
    }
    if ($reach['latitude_end'] !== null && $reach['longitude_end'] !== null) {
        $elat = number_format($reach['latitude_end'], 5, '.', '');
        $elon = number_format($reach['longitude_end'], 5, '.', '');
        $coord_fields['Take-out'] = [$elat, $elon];
        $map_points['Take-out'] = "$elat,$elon";
    }

    // Coord fields render as a single flex-wrap row instead of three
    // separate rows so on a wide screen they sit side-by-side. The CSS
    // (.coord-trio) wraps to vertical stack when the container is narrow.
    $coord_row_html = '';
    if ($coord_fields !== []) {
        $items = '';
        foreach ($coord_fields as $label => $coords) {
            $url = "https://www.google.com/maps?q={$coords[0]},{$coords[1]}";
            $items .= '<div class="coord-item">'
                . '<span class="coord-label">' . htmlspecialchars($label) . ':</span> '
                . '<a href="' . htmlspecialchars($url)
                . '" target="_blank" rel="noopener">'
                . htmlspecialchars("{$coords[0]}, {$coords[1]}") . '</a>'
                . '</div>';
        }
        $coord_row_html = '<tr><td colspan="2"><div class="coord-trio">'
            . $items
            . '</div></td></tr>';
    }

    $fields += [
        'Difficulties' => $reach['difficulties'],
        'Notes' => $reach['notes'],
    ];

    $track_color = _compute_description_track_color($related['flow_levels'], $readings);

    $has_map = false;
    $geom = $reach['geom'] ?? null;
    if (count($map_points) >= 1 || ($geom ?? '') !== '') {
        echo '</table>';
        $gauge_id_for_map = $gauge !== null ? $gauge['id'] : null;
        $has_map = gm_render_map($map_points, $geom, $track_color, [], $gauge_id_for_map);
        if (($reach['gradient_profile'] ?? '') !== '') {
            // Sits directly below the map, full container width, so the
            // cursor-linked map dot tracks visually with chart position.
            // Capture first and skip the wrapper when the SVG is '' (a
            // profile with < 2 samples) so we don't emit an empty div.
            $gp_svg = generate_gradient_profile_svg(
                (string)$reach['gradient_profile'],
                $reach['id'],
                length_mi: $reach['length'],
                putin_lat: $reach['latitude_start'],
                putin_lon: $reach['longitude_start'],
                takeout_lat: $reach['latitude_end'],
                takeout_lon: $reach['longitude_end'],
                putin_elev_ft: $reach['elevation'],
                elev_lost_ft: $reach['elevation_lost']
            );
            if ($gp_svg !== '') {
                echo '<div class="gradient-profile-container">' . $gp_svg . '</div>';
            }
        }
        echo '<table class="desc-table">';
    }

    $html_fields = ['Gauge'];
    $autolink_fields = ['Description', 'Notes'];
    foreach ($fields as $label => $value) {
        if ($value === null || trim($value) === '') {
            continue;
        }
        if (in_array($label, $html_fields, true)) {
            echo "<tr><td>$label</td><td>$value</td></tr>\n";
        } elseif (in_array($label, $autolink_fields, true)) {
            echo "<tr><td>$label</td><td>" . nl2br(autolink_urls($value)) . "</td></tr>\n";
        } else {
            $esc = htmlspecialchars($value);
            echo "<tr><td>$label</td><td>$esc</td></tr>\n";
        }
    }
    if ($coord_row_html !== '') {
        echo $coord_row_html;
    }

    echo '</table>';
    return $has_map;
}

/**
 * Pick a track color for the inline map based on the current readings
 * against the flow-level bands. Defaults to blue (unknown) if no
 * reading matches any level's data_type.
 *
 * @param list<array{level: string, low: float|null, low_data_type: string, high: float|null, high_data_type: string}> $flow_levels
 * @param list<array{data_type: string, value: float, observed_at: string, delta_per_hour: float|null}> $readings
 */
function _compute_description_track_color(array $flow_levels, array $readings): string
{
    $default = '#2196F3'; // blue = unknown
    if ($flow_levels === [] || $readings === []) {
        return $default;
    }
    $reading_by_type = [];
    foreach ($readings as $r) {
        $reading_by_type[$r['data_type']] = $r['value'];
    }
    foreach ($flow_levels as $fl) {
        $dtype = $fl['low_data_type'];
        if (!isset($reading_by_type[$dtype])) {
            continue;
        }
        $val = $reading_by_type[$dtype];
        $lo = $fl['low'];
        $hi = $fl['high'];
        $in_range = ($lo === null || $val >= $lo) && ($hi === null || $val <= $hi);
        if ($in_range) {
            $level_colors = ['low' => '#e8a735', 'okay' => '#4caf50', 'high' => '#e53935'];
            return $level_colors[$fl['level']];
        }
    }
    return $default;
}

/**
 * "Data Sources" section — one row per source feeding the linked gauge,
 * with USGS/NWRFC station-page links inferred from `agency` substring
 * (case-insensitive). Calc-expression sources get an autolinker that
 * cross-refs gauge-name tokens to other reaches.
 *
 * @param array{id: int, usgs_id: string|null, nwsli_id: string|null}|null $gauge
 */
function _render_data_sources(PDO $db, ?array $gauge): void
{
    if ($gauge === null) {
        return;
    }
    $src_stmt = $db->prepare(
        'SELECT s.name, s.agency, f.url AS fetch_url, c.expression AS calc_expr
         FROM source s
         JOIN gauge_source gs ON gs.source_id = s.id
         LEFT JOIN fetch_url f ON s.fetch_url_id = f.id
         LEFT JOIN calc_expression c ON s.calc_expression_id = c.id
         WHERE gs.gauge_id = ?'
    );
    $src_stmt->execute([$gauge['id']]);
    /** @var list<array{name: string, agency: string|null, fetch_url: string|null, calc_expr: string|null}> $sources */
    $sources = $src_stmt->fetchAll();

    if ($sources === []) {
        return;
    }

    echo '<h3 style="margin-top:1rem">Data Sources</h3>';
    echo '<table class="desc-table">';

    $station_urls = [];
    if ($gauge['usgs_id'] !== null && $gauge['usgs_id'] !== '') {
        $station_urls['USGS'] = [
            'label' => 'USGS - ' . $gauge['usgs_id'],
            'url' => "https://waterdata.usgs.gov/monitoring-location/USGS-"
                . urlencode($gauge['usgs_id'])
                . "/#dataTypeId=continuous-00065-0&period=P7D&showFieldMeasurements=true",
        ];
    }
    if ($gauge['nwsli_id'] !== null && $gauge['nwsli_id'] !== '') {
        $station_urls['NWRFC'] = [
            'label' => 'NWRFC - ' . $gauge['nwsli_id'],
            'url' => "https://www.nwrfc.noaa.gov/river/station/flowplot/flowplot.cgi?lid="
                . urlencode($gauge['nwsli_id']),
        ];
    }

    $shown_agencies = [];
    foreach ($sources as $src) {
        $matched = null;
        $agency = $src['agency'] ?? '';
        foreach ($station_urls as $key => $info) {
            if (in_array($key, $shown_agencies, true)) {
                continue;
            }
            if (stripos($agency, $key) !== false) {
                $matched = $key;
                break;
            }
            if ($key === 'NWRFC' && stripos($agency, 'NWS') !== false) {
                $matched = $key;
                break;
            }
        }

        if ($matched !== null) {
            $shown_agencies[] = $matched;
            // $matched is always a key of $station_urls — it came from the loop above.
            assert(isset($station_urls[$matched]));
            $info = $station_urls[$matched];
            $label = '<a href="' . htmlspecialchars($info['url'])
                . '" target="_blank" rel="noopener">'
                . htmlspecialchars($info['label']) . '</a>';
        } else {
            $src_name = htmlspecialchars($src['name']);
            $agency = ($src['agency'] !== null && $src['agency'] !== '') ? htmlspecialchars($src['agency']) : '';
            $label = $agency !== '' ? "$agency — $src_name" : $src_name;
        }

        if ($src['fetch_url'] !== null && $src['fetch_url'] !== '') {
            $url = htmlspecialchars($src['fetch_url']);
            echo "<tr><td>$label</td><td><a href=\"$url\" target=\"_blank\" rel=\"noopener\">$url</a></td></tr>\n";
        } elseif ($src['calc_expr'] !== null && $src['calc_expr'] !== '') {
            // Escape FIRST, then run the regex on the escaped string.
            // preg_replace_callback returns unmatched portions of the
            // input verbatim — without pre-escaping, any HTML
            // metacharacters between matches (or in malformed input)
            // would land on the page unescaped. \w+ matches word
            // characters only, which htmlspecialchars leaves untouched,
            // so the regex still locks onto the same substrings.
            $expr_safe = htmlspecialchars($src['calc_expr']);
            $expr_html = preg_replace_callback(
                '/(\w+)::(\w+)::(\w+)/',
                function ($m) use ($db) {
                    $gauge_name = $m[2];
                    $stmt = $db->prepare(
                        'SELECT r.id, r.display_name
                         FROM reach r
                         JOIN gauge g ON r.gauge_id = g.id
                         WHERE g.name = ?
                         LIMIT 1'
                    );
                    $stmt->execute([$gauge_name]);
                    /** @var array{id: int, display_name: string|null}|false $r */
                    $r = $stmt->fetch();
                    if ($r !== false) {
                        $display = htmlspecialchars(
                            ($r['display_name'] !== null && $r['display_name'] !== '')
                                ? $r['display_name']
                                : $gauge_name
                        );
                        $rhref = pubhash_url('description', $r['id']);
                        return "<a href=\"{$rhref}\" title=\"{$m[0]}\">$display</a>::{$m[3]}";
                    }
                    return $m[0];
                },
                $expr_safe
            );
            echo "<tr><td>$label</td><td>Calculated: $expr_html</td></tr>\n";
        } else {
            echo "<tr><td>$label</td><td>—</td></tr>\n";
        }
    }
    foreach ($station_urls as $key => $info) {
        if (!in_array($key, $shown_agencies, true)) {
            $label = '<a href="' . htmlspecialchars($info['url'])
                . '" target="_blank" rel="noopener">'
                . htmlspecialchars($info['label']) . '</a>';
            echo "<tr><td>$label</td><td></td></tr>\n";
        }
    }
    echo '</table>';
}

/**
 * Guidebooks sub-table plus an AW row when reach.aw_id is set.
 *
 * Skipped if neither the guidebook list nor reach.aw_id applies.
 * (Note: reach_detail.php has a nearly identical render — a follow-up
 * DRY pass could share the body via a parameter for the surrounding
 * button bar; see docs/done/PLAN_php_layer_split.md Tier 3 closeout.)
 *
 * @param array{aw_id: int|null} $reach
 */
function _render_description_guidebooks(PDO $db, array $reach, int $id): void
{
    $gb_stmt = $db->prepare(
        'SELECT g.title, g.subtitle, g.edition, g.author, g.url AS book_url,
                rg.page, rg.run, rg.url AS entry_url
         FROM reach_guidebook rg
         JOIN guidebook g ON g.id = rg.guidebook_id
         WHERE rg.reach_id = ?
         ORDER BY g.sort_order, g.title, g.edition'
    );
    $gb_stmt->execute([$id]);
    /** @var list<array{title: string, subtitle: string|null, edition: string|null, author: string|null, book_url: string|null, page: string|null, run: string|null, entry_url: string|null}> $guidebooks */
    $guidebooks = $gb_stmt->fetchAll();

    if ($guidebooks === [] && $reach['aw_id'] === null) {
        return;
    }
    echo '<h3 style="margin-top:1rem">Guidebooks</h3>';
    echo '<table class="desc-table">';
    if ($reach['aw_id'] !== null) {
        $aw_url = "https://www.americanwhitewater.org/content/River/view/river-detail/"
            . intval($reach['aw_id']) . "/";
        echo '<tr><td><a href="' . htmlspecialchars($aw_url)
            . '" target="_blank" rel="noopener">American Whitewater</a></td><td></td></tr>' . "\n";
    }
    foreach ($guidebooks as $gb) {
        $title = htmlspecialchars($gb['title']);
        if ($gb['subtitle'] !== null && $gb['subtitle'] !== '') {
            $title .= ' — ' . htmlspecialchars($gb['subtitle']);
        }
        if ($gb['edition'] !== null && $gb['edition'] !== '') {
            $title .= ' (' . htmlspecialchars($gb['edition']) . ')';
        }
        $url = ($gb['entry_url'] !== null && $gb['entry_url'] !== '') ? $gb['entry_url'] : $gb['book_url'];
        if ($url !== null && $url !== '') {
            $title = '<a href="' . htmlspecialchars($url) . '" target="_blank" rel="noopener">' . $title . '</a>';
        }
        $detail = [];
        if ($gb['page'] !== null && $gb['page'] !== '') {
            $detail[] = 'p. ' . htmlspecialchars($gb['page']);
        }
        if ($gb['run'] !== null && $gb['run'] !== '') {
            $detail[] = 'run ' . htmlspecialchars($gb['run']);
        }
        echo "<tr><td>$title</td><td>" . implode(', ', $detail) . "</td></tr>\n";
    }
    echo '</table>';
}

/**
 * Footer button bar — Back to index, Reach details, plus an
 * Edit/Suggest-edit/Sign-in-to-suggest button conditional on the
 * editor feature flag + the current editor's role.
 */
function _render_description_footer(int $id): void
{
    $btn_style = 'display:inline-flex;align-items:center;min-height:44px;padding:8px 12px';
    echo '<nav style="margin-top:1rem;display:flex;flex-wrap:wrap;gap:.5rem">';
    echo '<a href="/index.html" style="' . $btn_style . '">Back to main page</a>';
    echo '<a href="' . pubhash_url('reach', $id) . '" style="' . $btn_style . '">Reach details</a>';
    if (editor_feature_enabled()) {
        $editor = current_editor();
        if (is_maintainer($editor)) {
            echo '<a href="' . pubhash_url('edit', $id) . '" style="' . $btn_style . '">Edit</a>';
        } elseif ($editor !== null) {
            echo '<a href="/propose.php?type=reach&amp;h=' . pubhash_encode($id)
                . '" style="' . $btn_style . '">Suggest an edit</a>';
        } else {
            $next = rawurlencode("/propose.php?type=reach&h=" . pubhash_encode($id));
            echo '<a href="/login.php?next=' . $next . '" style="'
                . $btn_style . '">Sign in to suggest an edit</a>';
        }
    }
    echo '</nav>';
}
