<?php
declare(strict_types=1);
/**
 * Detail mode for /reach.php — renders a single reach's full page.
 *
 * Called from reach.php after arg-parse + default-fallback. Loads the
 * reach + navigation context + related data (gauge, states, classes,
 * flow levels, guidebooks), then renders the nav bar, details table,
 * sub-tables, optional map, and footer.
 *
 * Convention matches the other helpers in this directory: function-only
 * (no top-level side effects beyond require_once), snake_case names,
 * strict types, helpers prefixed with `_` are file-private.
 *
 * DB-row array shapes (the reach `SELECT *`, the joined guidebook/class
 * rows, etc.) are spelled out inline in each `@param`/`@return`/`@var`
 * below — verified against the dev DB — so `mixed`-typed offset access
 * resolves without leaning on shared helper return types.
 */

require_once __DIR__ . '/header.php';
require_once __DIR__ . '/pubhash_request.php';
require_once __DIR__ . '/footer.php';
require_once __DIR__ . '/html.php';
require_once __DIR__ . '/gauge_map.php';
require_once __DIR__ . '/svg_plot.php';
require_once __DIR__ . '/reach_fields.php';

/**
 * Dispatch detail mode and write the full HTTP response.
 *
 * 404s via `http_terminate(404, 'Reach not found')` if the id has no
 * reach row (preserving the original inline plain-text 404 — Phase 2.5
 * may switch this to get_reach_or_404 for a richer HTML 404 page).
 *
 * $q and $st are the entry-point's query/state filters; in detail mode
 * they are always empty (search mode would have exited before getting
 * here) but the nav bar's embedded search form re-echoes them, so they
 * flow through as parameters rather than being defaulted.
 */
function handle_reach_detail(
    PDO $db,
    int $id,
    int $hidden,
    string $q,
    string $st,
    string $compact_css,
): void {
    $reach = _load_reach_or_404($db, $id);
    $name = (string)(($reach['display_name'] ?? '') !== '' ? $reach['display_name'] : $reach['name']);
    $nav = _load_reach_navigation($db, $reach, $id, $hidden);
    $related = _load_reach_related($db, $reach, $id);

    header('Cache-Control: no-cache');
    $preconnects = '<link rel="preconnect" href="https://a.tile.opentopomap.org">'
        . '<link rel="preconnect" href="https://b.tile.opentopomap.org">'
        . '<link rel="preconnect" href="https://c.tile.opentopomap.org">';
    include_header(
        $name . ' - Reach',
        '',
        '',
        $preconnects . gm_head_links() . $compact_css,
    );

    _render_reach_nav_bar(
        $db,
        $id,
        $hidden,
        $q,
        $st,
        $nav['prev'],
        $nav['next'],
        $nav['position'],
        $nav['total'],
    );

    $h2_text = htmlspecialchars($name);
    $location = trim($reach['description'] ?? '');
    if ($location !== '') {
        $h2_text .= ' -- ' . htmlspecialchars($location);
    }
    echo '<h2><a href="' . pubhash_url('description', $id) . '">' . $h2_text . '</a></h2>';

    _render_reach_details_table($reach, $related['states'], $related['classes'], $related['flow_levels']);
    _render_reach_class_ranges($related['classes']);
    _render_reach_guidebooks($reach, $related['guidebooks']);
    _render_reach_linked_gauge($related['gauge']);
    [$has_map, $map_scripts] = _render_reach_map($reach, $related['gauge']);
    if (($reach['gradient_profile'] ?? '') !== '') {
        // Capture first: generate_gradient_profile_svg returns '' for a
        // profile with < 2 samples (very short reaches). Skip the wrapper
        // in that case so we don't emit an empty container div.
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

    echo '<p style="margin-top:1rem">';
    echo '<a href="' . pubhash_url('description', $id) . '">Description</a>';
    echo ' | <a href="' . pubhash_url('data', $id) . '">Data inspector</a>';
    echo ' | <a href="/index.html">Back to main page</a></p>';

    if ($has_map) {
        echo $map_scripts;
    }
    include_footer();
}

/**
 * Load a reach by id or 404 via http_terminate(). Behavior matches the
 * pre-extraction inline check (plain-text 404 body); see header docblock
 * about the planned Phase 2.5 migration to get_reach_or_404. The
 * http_terminate seam lets in-process tests catch the early-out.
 *
 * @return array{id: int, updated_at: string|null, gauge_id: int|null,
 *     name: string|null, display_name: string|null, sort_name: string|null,
 *     nature: string|null, description: string|null, difficulties: string|null,
 *     basin: string|null, basin_area: float|null, elevation: float|null,
 *     elevation_lost: float|null, length: float|null, gradient: float|null,
 *     features: string|null, latitude: float|null, longitude: float|null,
 *     latitude_start: float|null, longitude_start: float|null, latitude_end: float|null,
 *     longitude_end: float|null, no_show: int, notes: string|null, optimal_flow: float|null,
 *     region: string|null, remoteness: string|null, scenery: string|null, season: string|null,
 *     watershed_type: string|null, aw_id: int|null, river: string|null, max_gradient: float|null,
 *     geom: string|null, huc: string|null, map_only: int, no_flow_range: int,
 *     gradient_profile: string|null, gradient_unreliable: int}
 */
function _load_reach_or_404(PDO $db, int $id): array
{
    $stmt = $db->prepare('SELECT * FROM reach WHERE id = ?');
    $stmt->execute([$id]);
    /** @var array{id: int, updated_at: string|null, gauge_id: int|null,
     *     name: string|null, display_name: string|null, sort_name: string|null,
     *     nature: string|null, description: string|null, difficulties: string|null,
     *     basin: string|null, basin_area: float|null, elevation: float|null,
     *     elevation_lost: float|null, length: float|null, gradient: float|null,
     *     features: string|null, latitude: float|null, longitude: float|null,
     *     latitude_start: float|null, longitude_start: float|null, latitude_end: float|null,
     *     longitude_end: float|null, no_show: int, notes: string|null, optimal_flow: float|null,
     *     region: string|null, remoteness: string|null, scenery: string|null, season: string|null,
     *     watershed_type: string|null, aw_id: int|null, river: string|null, max_gradient: float|null,
     *     geom: string|null, huc: string|null, map_only: int, no_flow_range: int,
     *     gradient_profile: string|null, gradient_unreliable: int}|false $reach */
    $reach = $stmt->fetch();
    if ($reach === false) {
        http_terminate(404, 'Reach not found');
    }
    return $reach;
}

/**
 * Prev/next reach ids by sort_name (tie-broken by id), plus total +
 * current position. All four queries respect the no_show / hidden flag.
 *
 * Only $reach['sort_name'] (string|null) is read here, so the param is
 * narrowed to just that key rather than the full reach row.
 *
 * @param  array{sort_name: string|null} $reach
 * @return array{
 *     prev: array{id: int}|false,
 *     next: array{id: int}|false,
 *     position: int|string,
 *     total: int|string
 * }
 */
function _load_reach_navigation(PDO $db, array $reach, int $id, int $hidden): array
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
 * Gauge (or null), states, classes, derived flow_levels, and guidebooks
 * for the current reach.
 *
 * Only $reach['gauge_id'] (int|null) is read here.
 *
 * @param  array{gauge_id: int|null} $reach
 * @return array{
 *     gauge: array{id: int, name: string, bank_full: float|null, flood_stage: float|null,
 *         location: string|null, latitude: float|null, longitude: float|null,
 *         station_id: string|null, cbtt_id: string|null, geos_id: string|null,
 *         nws_id: string|null, nwsli_id: string|null, snotel_id: string|null,
 *         usgs_id: string|null, rating_id: int|null, elevation: float|null,
 *         drainage_area: float|null, huc: string|null, allow_negative_flow: int,
 *         river: string|null, display_name: string|null, sort_name: string|null,
 *         state: string|null}|null,
 *     states: list<string>,
 *     classes: list<array{id: int, reach_id: int, name: string, low: float|null,
 *         low_data_type: string|null, high: float|null, high_data_type: string|null}>,
 *     flow_levels: list<array{level: string, low: float|null, low_data_type: string,
 *         high: float|null, high_data_type: string}>,
 *     guidebooks: list<array{title: string, subtitle: string|null, edition: string|null,
 *         author: string|null, book_url: string|null, page: string|null, run: string|null,
 *         entry_url: string|null}>
 * }
 */
function _load_reach_related(PDO $db, array $reach, int $id): array
{
    $gauge = null;
    if ($reach['gauge_id'] !== null) {
        $stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
        $stmt->execute([$reach['gauge_id']]);
        /** @var array{id: int, name: string, bank_full: float|null, flood_stage: float|null,
         *     location: string|null, latitude: float|null, longitude: float|null,
         *     station_id: string|null, cbtt_id: string|null, geos_id: string|null,
         *     nws_id: string|null, nwsli_id: string|null, snotel_id: string|null,
         *     usgs_id: string|null, rating_id: int|null, elevation: float|null,
         *     drainage_area: float|null, huc: string|null, allow_negative_flow: int,
         *     river: string|null, display_name: string|null, sort_name: string|null,
         *     state: string|null}|false $g */
        $g = db_row($stmt);
        $gauge = $g === false ? null : $g;
    }

    $states_stmt = $db->prepare(
        'SELECT s.name FROM state s JOIN reach_state rs ON s.id = rs.state_id WHERE rs.reach_id = ?'
    );
    $states_stmt->execute([$id]);
    $states = array_map('strval', array_column($states_stmt->fetchAll(), 'name'));

    $classes_stmt = $db->prepare('SELECT * FROM reach_class WHERE reach_id = ?');
    $classes_stmt->execute([$id]);
    /** @var list<array{id: int, reach_id: int, name: string, low: float|null,
     *     low_data_type: string|null, high: float|null, high_data_type: string|null}> $classes */
    $classes = db_rows($classes_stmt);

    $flow_levels = _derive_reach_flow_levels($db, $id);

    $gb_stmt = $db->prepare(
        'SELECT g.title, g.subtitle, g.edition, g.author, g.url AS book_url,
                rg.page, rg.run, rg.url AS entry_url
         FROM reach_guidebook rg
         JOIN guidebook g ON g.id = rg.guidebook_id
         WHERE rg.reach_id = ?
         ORDER BY g.sort_order, g.title, g.edition'
    );
    $gb_stmt->execute([$id]);
    /** @var list<array{title: string, subtitle: string|null, edition: string|null,
     *     author: string|null, book_url: string|null, page: string|null, run: string|null,
     *     entry_url: string|null}> $guidebooks */
    $guidebooks = db_rows($gb_stmt);

    return [
        'gauge' => $gauge,
        'states' => $states,
        'classes' => $classes,
        'flow_levels' => $flow_levels,
        'guidebooks' => $guidebooks,
    ];
}

/**
 * Derive low/okay/high flow-level bands from the reach's primary class
 * range (the first reach_class row with populated low/high bounds).
 * Returns the shape the renderer needs — same as when reach_level was
 * the source of truth, kept stable so downstream JSON/HTML doesn't shift.
 *
 * @return list<array{level: string, low: float|null, low_data_type: string,
 *     high: float|null, high_data_type: string}>  Empty if no class row has bounds.
 */
function _derive_reach_flow_levels(PDO $db, int $id): array
{
    $class_range_stmt = $db->prepare(
        'SELECT low, low_data_type, high, high_data_type
         FROM reach_class
         WHERE reach_id = ? AND (low IS NOT NULL OR high IS NOT NULL)
         ORDER BY id LIMIT 1'
    );
    $class_range_stmt->execute([$id]);
    /** @var array{low: float|null, low_data_type: string|null, high: float|null, high_data_type: string|null}|false $class_range */
    $class_range = $class_range_stmt->fetch();
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
 * Render the top navigation bar: prev/next links, "Reach N of M",
 * embedded search form (q + state select + Go), and the show-hidden
 * toggle link. Queries the state list inline (one cheap SELECT for
 * the <select> options).
 *
 * @param array{id: int}|false $prev
 * @param array{id: int}|false $next
 * @param int|string           $position
 * @param int|string           $total
 */
function _render_reach_nav_bar(
    PDO $db,
    int $id,
    int $hidden,
    string $q,
    string $st,
    array|false $prev,
    array|false $next,
    int|string $position,
    int|string $total,
): void {
    echo '<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;flex-wrap:wrap">';
    $hq = $hidden !== 0 ? '&amp;hidden=1' : '';
    if ($prev !== false) {
        echo '<a href="' . pubhash_url('reach', $prev['id'], $hq) . '">&laquo; Prev</a>';
    } else {
        echo '<span style="color:#999">&laquo; Prev</span>';
    }
    echo "<span>Reach $position of $total</span>";
    if ($next !== false) {
        echo '<a href="' . pubhash_url('reach', $next['id'], $hq) . '">Next &raquo;</a>';
    } else {
        echo '<span style="color:#999">Next &raquo;</span>';
    }
    $all_states = db_query($db, 'SELECT abbreviation FROM state ORDER BY abbreviation')
        ->fetchAll(PDO::FETCH_COLUMN);
    echo '<form method="get" action="/reach.php" style="display:flex;gap:.25rem;margin-left:auto">';
    echo '<input type="text" name="q" placeholder="Search reaches…" style="width:14rem"'
        . ' value="' . htmlspecialchars($q) . '">';
    echo '<label for="st" class="sr-only">State</label>';
    echo '<select name="st" id="st"><option value="">All states</option>';
    foreach ($all_states as $s) {
        $sel = ($st === $s) ? ' selected' : '';
        $esc = htmlspecialchars($s);
        echo "<option value=\"$esc\"$sel>$esc</option>";
    }
    echo '</select>';
    if ($hidden !== 0) {
        echo '<input type="hidden" name="hidden" value="1">';
    }
    echo '<button type="submit">Go</button>';
    echo '</form>';
    $toggle_hidden = $hidden !== 0 ? 0 : 1;
    $toggle_label = $hidden !== 0 ? 'Show visible' : 'Show hidden';
    echo '<a href="' . pubhash_url('reach', $id, "&amp;hidden=$toggle_hidden") . '">' . $toggle_label . '</a>';
    echo '</div>';
}

/**
 * Render the main reach details table — one row per populated field.
 * Coordinate fields become Google Maps anchor links. Description and
 * Notes go through autolink_urls (URLs become clickable).
 *
 * @param array{id: int, name: string|null, display_name: string|null, river: string|null,
 *     basin_area: float|null, season: string|null, nature: string|null,
 *     watershed_type: string|null, scenery: string|null, remoteness: string|null,
 *     features: string|null, optimal_flow: float|null, no_show: int, updated_at: string|null,
 *     latitude_start: float|null, longitude_start: float|null, latitude_end: float|null,
 *     longitude_end: float|null, description: string|null, difficulties: string|null,
 *     notes: string|null, length: float|null, elevation: float|null,
 *     elevation_lost: float|null, gradient: float|null, max_gradient: float|null,
 *     huc: string|null, basin: string|null} $reach
 * @param list<string> $states
 * @param list<array{id: int, reach_id: int, name: string, low: float|null,
 *     low_data_type: string|null, high: float|null, high_data_type: string|null}> $classes
 * @param list<array{level: string, low: float|null, low_data_type: string,
 *     high: float|null, high_data_type: string}> $flow_levels
 */
function _render_reach_details_table(array $reach, array $states, array $classes, array $flow_levels): void
{
    echo '<table class="desc-table">';
    $fields = [
        'ID' => $reach['id'],
        'Name' => $reach['name'],
        'Display Name' => $reach['display_name'],
        'River' => $reach['river'],
        'Class' => implode(', ', array_column($classes, 'name')),
        'Watershed' => format_reach_watershed($reach, $states),
        'Watershed Area' => (bool)$reach['basin_area'] ? number_format($reach['basin_area'], 1) . ' sq mi' : null,
        'Season' => $reach['season'],
        'Nature' => $reach['nature'],
        'Watershed type' => $reach['watershed_type'],
        'Scenery' => $reach['scenery'],
        'Remoteness' => $reach['remoteness'],
        'Features' => $reach['features'],
        'Length' => format_reach_length($reach),
        'Elevation' => format_reach_elevation($reach),
        'Flow' => format_reach_flow($flow_levels),
        'Optimal Flow' => (bool)$reach['optimal_flow'] ? number_format($reach['optimal_flow'], 0) . ' CFS' : null,
        'No Show' => (bool)$reach['no_show'] ? 'Yes' : null,
        'Updated' => $reach['updated_at'],
    ];

    if ($reach['latitude_start'] !== null && $reach['longitude_start'] !== null) {
        $lat = number_format($reach['latitude_start'], 6, '.', '');
        $lon = number_format($reach['longitude_start'], 6, '.', '');
        $url = "https://www.google.com/maps?q=$lat,$lon";
        $fields['Put-in'] = "<a href=\"" . htmlspecialchars($url)
            . "\" target=\"_blank\" rel=\"noopener\">$lat, $lon</a>";
    }
    if ($reach['latitude_end'] !== null && $reach['longitude_end'] !== null) {
        $lat = number_format($reach['latitude_end'], 6, '.', '');
        $lon = number_format($reach['longitude_end'], 6, '.', '');
        $url = "https://www.google.com/maps?q=$lat,$lon";
        $fields['Take-out'] = "<a href=\"" . htmlspecialchars($url)
            . "\" target=\"_blank\" rel=\"noopener\">$lat, $lon</a>";
    }

    $fields += [
        'Description' => $reach['description'],
        'Difficulties' => $reach['difficulties'],
        'Notes' => $reach['notes'],
    ];

    $html_fields = ['Put-in', 'Take-out'];
    $autolink_fields = ['Description', 'Notes'];
    foreach ($fields as $label => $value) {
        if ($value === null || trim((string)$value) === '') {
            continue;
        }
        if (in_array($label, $html_fields, true)) {
            echo "<tr><td>$label</td><td>$value</td></tr>\n";
        } elseif (in_array($label, $autolink_fields, true)) {
            echo "<tr><td>$label</td><td>" . nl2br(autolink_urls((string)$value)) . "</td></tr>\n";
        } else {
            $esc = htmlspecialchars((string)$value);
            echo "<tr><td>$label</td><td>$esc</td></tr>\n";
        }
    }
    echo '</table>';
}

/**
 * "Class Ranges" sub-table — one row per class, with its low/high
 * bounds + data_type units. Skipped entirely if no class row has
 * either bound populated.
 *
 * @param list<array{id: int, reach_id: int, name: string, low: float|null,
 *     low_data_type: string|null, high: float|null, high_data_type: string|null}> $classes
 */
function _render_reach_class_ranges(array $classes): void
{
    if ($classes === []) {
        return;
    }
    $has_ranges = false;
    foreach ($classes as $c) {
        if ($c['low'] !== null || $c['high'] !== null) {
            $has_ranges = true;
            break;
        }
    }
    if (!$has_ranges) {
        return;
    }
    echo '<h3 style="margin-top:1rem">Class Ranges</h3>';
    echo '<table class="desc-table">';
    echo '<tr><th>Class</th><th>Low</th><th>High</th></tr>';
    foreach ($classes as $c) {
        $cname = htmlspecialchars($c['name']);
        $lo = $c['low'] !== null ? number_format($c['low'], 1) : '';
        $hi = $c['high'] !== null ? number_format($c['high'], 1) : '';
        if ($c['low_data_type'] !== null && $c['low_data_type'] !== '') {
            $lo .= ' ' . htmlspecialchars($c['low_data_type']);
        }
        if ($c['high_data_type'] !== null && $c['high_data_type'] !== '') {
            $hi .= ' ' . htmlspecialchars($c['high_data_type']);
        }
        echo "<tr><td>$cname</td><td>$lo</td><td>$hi</td></tr>\n";
    }
    echo '</table>';
}

/**
 * "Guidebooks" sub-table — one row per guidebook entry, plus an extra
 * American Whitewater row when reach.aw_id is set (regardless of whether
 * a reach_guidebook row references AW). Skipped if neither source applies.
 *
 * Only $reach['aw_id'] (int|null) is read here.
 *
 * @param array{aw_id: int|null} $reach
 * @param list<array{title: string, subtitle: string|null, edition: string|null,
 *     author: string|null, book_url: string|null, page: string|null, run: string|null,
 *     entry_url: string|null}> $guidebooks
 */
function _render_reach_guidebooks(array $reach, array $guidebooks): void
{
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
        $url = ($gb['entry_url'] ?? '') !== '' ? $gb['entry_url'] : $gb['book_url'];
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
 * "Linked Gauge" sub-table — a single-row entry pointing at /gauge.php
 * with optional Location row. Skipped if the reach has no gauge_id.
 *
 * @param array{id: int, name: string, location: string|null,
 *     display_name: string|null}|null $gauge
 */
function _render_reach_linked_gauge(?array $gauge): void
{
    if ($gauge === null) {
        return;
    }
    echo '<h3 style="margin-top:1rem">Linked Gauge</h3>';
    echo '<table class="desc-table">';
    $gname = htmlspecialchars((string)(($gauge['display_name'] ?? '') !== '' ? $gauge['display_name'] : $gauge['name']));
    $gloc = htmlspecialchars($gauge['location'] ?? '');
    $ghref = pubhash_url('gauge', $gauge['id']);
    echo "<tr><td>Gauge</td><td><a href=\"{$ghref}\">$gname</a></td></tr>\n";
    if ($gloc !== '') {
        echo "<tr><td>Location</td><td>$gloc</td></tr>\n";
    }
    echo '</table>';
}

/**
 * Emit the <div id="reach-map"> element with Put-in / Take-out / Gauge
 * point markers and an optional GPS track polyline. Returns
 * [has_map, deferred_script_tags] so the caller can append the Leaflet
 * <script> tags after the footer.
 *
 * @param  array{latitude_start: float|null, longitude_start: float|null,
 *     latitude_end: float|null, longitude_end: float|null, geom: string|null} $reach
 * @param  array{id: int, latitude: float|null, longitude: float|null}|null $gauge
 * @return array{0: bool, 1: string}
 */
function _render_reach_map(array $reach, ?array $gauge): array
{
    $map_points = [];
    if ($reach['latitude_start'] !== null && $reach['longitude_start'] !== null) {
        $map_points['Put-in'] = number_format($reach['latitude_start'], 6, '.', '')
            . ',' . number_format($reach['longitude_start'], 6, '.', '');
    }
    if ($reach['latitude_end'] !== null && $reach['longitude_end'] !== null) {
        $map_points['Take-out'] = number_format($reach['latitude_end'], 6, '.', '')
            . ',' . number_format($reach['longitude_end'], 6, '.', '');
    }
    if ($gauge !== null && $gauge['latitude'] !== null && $gauge['longitude'] !== null) {
        $map_points['Gauge'] = number_format($gauge['latitude'], 6, '.', '')
            . ',' . number_format($gauge['longitude'], 6, '.', '');
    }

    if ($map_points === [] && ($reach['geom'] ?? '') === '') {
        return [false, ''];
    }

    $track = null;
    if (isset($reach['geom']) && $reach['geom'] !== '') {
        $track = [];
        foreach (explode(',', $reach['geom']) as $pair) {
            $split = preg_split('/\s+/', trim($pair));
            $parts = $split !== false ? $split : [];
            if (count($parts) === 2) {
                $track[] = [(float)$parts[1], (float)$parts[0]];
            }
        }
    }

    $pts_json = htmlspecialchars((string)json_encode($map_points));
    echo '<div id="reach-map" style="height:400px;margin-top:1rem;border:1px solid #ccc"'
        . ' data-points="' . $pts_json . '"';
    if ($gauge !== null) {
        echo ' data-gauge-id="' . $gauge['id'] . '"';
    }
    if (($track ?? []) !== []) {
        $track_json = htmlspecialchars((string)json_encode($track));
        echo ' data-track="' . $track_json . '"';
    }
    echo '></div>';

    $doc_root = is_string($_SERVER['DOCUMENT_ROOT'] ?? null) ? $_SERVER['DOCUMENT_ROOT'] : '';
    $gp_raw = @filemtime($doc_root . '/static/gradient-profile.js');
    $gp_mtime = $gp_raw !== false ? $gp_raw : 1;
    $fm_raw = @filemtime($doc_root . '/static/feature-map.js');
    $fm_mtime = $fm_raw !== false ? $fm_raw : 1;
    return [
        true,
        '<script src="/static/leaflet.js" defer></script>'
        . '<script src="/static/feature-map.js?v=' . $fm_mtime . '" defer></script>'
        // gradient-profile.js degrades to chart-only tooltip if the map
        // handle isn't there, so it's safe to ship on any reach page —
        // no-op when the page has no .gradient-profile-chart elements.
        . '<script src="/static/gradient-profile.js?v=' . $gp_mtime . '" defer></script>',
    ];
}
