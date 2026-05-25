<?php

declare(strict_types=1);

/**
 * Detail page rendering for /gauge.php?id=N — readings, plots, map,
 * gauge metadata, associated sources, associated reaches.
 *
 * Called from gauge.php after arg-parse / search-dispatch / default-
 * fallback. Loads the gauge + navigation context + related data
 * (sources, reaches, per-reach class thresholds for status classification,
 * latest gauge observations) and renders the full HTML response.
 *
 * Convention matches the other helpers in this directory: function-only
 * (no top-level side effects beyond require_once), snake_case names,
 * strict types, helpers prefixed with `_` are file-private.
 */

require_once __DIR__ . '/header.php';
require_once __DIR__ . '/footer.php';
require_once __DIR__ . '/gauge_plots.php';
require_once __DIR__ . '/gauge_map.php';
require_once __DIR__ . '/auth.php';

/**
 * Dispatch detail mode and write the full HTTP response.
 *
 * 404s with the plain `http_response_code(404); exit('Gauge not found')`
 * shape preserved verbatim from pre-extraction (no get_gauge_or_404
 * helper exists yet — could be added as a follow-up matching
 * get_reach_or_404).
 */
function handle_gauge_detail(PDO $db, int $id, ?string $start_date, ?string $end_date): void
{
    $gauge = _load_gauge_or_404($db, $id);
    // Prefer the normalized display_name populated by
    // scripts/seed_gauge_display.py; fall back to the internal canonical
    // `name` when the row predates the seeder.
    $gauge_display = $gauge['display_name'] ?: $gauge['name'];

    $nav = _load_gauge_navigation($db, $id);
    $related = _load_gauge_associated($db, $id);
    $readings = _load_gauge_readings($db, (int)$gauge['id']);

    // dtype → numeric value map for downstream reach-status classification.
    $readings_by_dtype = [];
    foreach ($readings as $r) {
        if ($r['value'] !== null) {
            $readings_by_dtype[(string)$r['data_type']] = (float)$r['value'];
        }
    }
    $reach_status_by_id = _compute_reach_statuses(
        $related['reaches'],
        $related['reach_class_thresholds'],
        $readings_by_dtype,
    );

    _render_gauge_header($gauge, $gauge_display);
    _render_gauge_nav_bar(
        $nav['position'],
        $nav['total'],
        $nav['prev'],
        $nav['next'],
    );
    echo '<h2>' . htmlspecialchars($gauge_display) . '</h2>';

    _render_stale_or_empty_banner($readings);
    _render_gauge_readings_table($readings);

    if ($readings) {
        _render_gauge_date_form_and_plots($db, $gauge, $id, $gauge_display, $start_date, $end_date);
    }

    $has_map = _render_gauge_map($gauge, $related['reaches'], $reach_status_by_id);

    _render_gauge_details_table($gauge);
    _render_associated_sources($related['sources']);
    _render_associated_reaches($related['reaches'], $reach_status_by_id);

    // Regression analysis (when present) sits below the primary content —
    // most users don't care about fit diagnostics.
    _render_gauge_regression($db, (string)$gauge['name'], $related['sources']);

    _render_gauge_footer($id, $has_map);

    include_footer();
}

/**
 * Load a gauge by id or write a 404 + exit. Preserves the pre-extraction
 * inline check (plain text 404 body — see header docblock about the
 * deferred get_gauge_or_404 helper).
 *
 * @return array<string, mixed>
 */
function _load_gauge_or_404(PDO $db, int $id): array
{
    $stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
    $stmt->execute([$id]);
    $gauge = $stmt->fetch();
    if (!$gauge) {
        http_response_code(404);
        exit('Gauge not found');
    }
    return $gauge;
}

/**
 * Prev/next gauge ids by id (ascending), plus total + current position.
 * Simpler than reach_detail's (no sort_name tie-break, no `no_show`
 * filter — gauges have no equivalent flag yet).
 *
 * @return array{
 *     prev: array<string, mixed>|false,
 *     next: array<string, mixed>|false,
 *     position: int|string,
 *     total: int|string
 * }
 */
function _load_gauge_navigation(PDO $db, int $id): array
{
    $prev_stmt = $db->prepare('SELECT id FROM gauge WHERE id < ? ORDER BY id DESC LIMIT 1');
    $prev_stmt->execute([$id]);
    $prev = db_row($prev_stmt);

    $next_stmt = $db->prepare('SELECT id FROM gauge WHERE id > ? ORDER BY id ASC LIMIT 1');
    $next_stmt->execute([$id]);
    $next = db_row($next_stmt);

    $total = (int)db_query($db, 'SELECT COUNT(*) FROM gauge')->fetchColumn();

    $pos_stmt = $db->prepare('SELECT COUNT(*) FROM gauge WHERE id <= ?');
    $pos_stmt->execute([$id]);
    $position = (int)$pos_stmt->fetchColumn();

    return ['prev' => $prev, 'next' => $next, 'position' => $position, 'total' => $total];
}

/**
 * Associated sources + reaches (with class list pulled from reach_class,
 * the canonical source) + per-reach low/high class thresholds for
 * status classification. The thresholds query is batched (IN clause) to
 * avoid N+1 on gauges with many reaches.
 *
 * @return array{
 *     sources: list<array<string, mixed>>,
 *     reaches: list<array<string, mixed>>,
 *     reach_class_thresholds: array<int, list<array<string, mixed>>>
 * }
 */
function _load_gauge_associated(PDO $db, int $id): array
{
    $sources_stmt = $db->prepare(
        'SELECT s.id, s.name, s.agency,
                (SELECT COUNT(*) FROM observation o WHERE o.source_id = s.id) AS obs_count,
                (SELECT SUBSTR(MAX(o.observed_at), 1, 10) FROM observation o WHERE o.source_id = s.id) AS latest_at,
                c.provenance_slug AS calc_provenance_slug
         FROM source s
         JOIN gauge_source gs ON s.id = gs.source_id
         LEFT JOIN calc_expression c ON s.calc_expression_id = c.id
         WHERE gs.gauge_id = ?
         ORDER BY s.name'
    );
    $sources_stmt->execute([$id]);
    $sources = db_rows($sources_stmt);

    // Pull class names from reach_class (the canonical source) rather than
    // the rarely-populated reach.difficulties column.
    $reaches_stmt = $db->prepare(
        'SELECT r.id, COALESCE(NULLIF(r.display_name, \'\'), r.name) AS name, r.river, r.length, r.basin, r.geom, r.description,
                (SELECT GROUP_CONCAT(rc.name, \', \') FROM reach_class rc WHERE rc.reach_id = r.id) AS classes
         FROM reach r WHERE r.gauge_id = ? ORDER BY r.sort_name'
    );
    $reaches_stmt->execute([$id]);
    $reaches = db_rows($reaches_stmt);

    $reach_class_thresholds = [];
    if ($reaches) {
        $reach_ids = array_map(static fn($r) => (int)$r['id'], $reaches);
        $ph = implode(',', array_fill(0, count($reach_ids), '?'));
        $thr_stmt = $db->prepare(
            "SELECT reach_id, low, high, low_data_type, high_data_type
             FROM reach_class
             WHERE reach_id IN ($ph) AND (low IS NOT NULL OR high IS NOT NULL)
             ORDER BY id"
        );
        $thr_stmt->execute($reach_ids);
        foreach (db_rows($thr_stmt) as $row) {
            $reach_class_thresholds[(int)$row['reach_id']][] = $row;
        }
    }

    return [
        'sources' => $sources,
        'reaches' => $reaches,
        'reach_class_thresholds' => $reach_class_thresholds,
    ];
}

/**
 * Latest gauge observations (one row per data_type) for the readings
 * table and the per-reach status-classification path.
 *
 * @return list<array<string, mixed>>
 */
function _load_gauge_readings(PDO $db, int $gauge_id): array
{
    $readings_stmt = $db->prepare(
        'SELECT data_type, value, observed_at, delta_per_hour
         FROM latest_gauge_observation WHERE gauge_id = ?'
    );
    $readings_stmt->execute([$gauge_id]);
    return db_rows($readings_stmt);
}

/**
 * Classify a reach against its class-range thresholds, given the gauge's
 * current readings. Mirrors db/reaches.py::classify_level + the priority
 * order in build.py::_get_row_data — try (flow, gauge, inflow-as-flow)
 * and pick the first dtype with both a reading on this gauge and a class
 * threshold whose data_type matches.
 *
 * @param  list<array<string, mixed>> $thresholds        Class rows with low/high bounds.
 * @param  array<string, float>       $readings_by_dtype Reading values keyed by data_type.
 * @return 'low'|'okay'|'high'|'unknown'
 */
function _classify_reach_status(array $thresholds, array $readings_by_dtype): string
{
    $candidates = [['flow', 'flow'], ['gauge', 'gauge'], ['inflow', 'flow']];
    foreach ($candidates as [$reading_dt, $classify_dt]) {
        if (!isset($readings_by_dtype[$reading_dt])) {
            continue;
        }
        $v = $readings_by_dtype[$reading_dt];
        foreach ($thresholds as $rc) {
            if ($rc['low'] === null && $rc['high'] === null) {
                continue;
            }
            if (!empty($rc['low_data_type']) && $rc['low_data_type'] !== $classify_dt) {
                continue;
            }
            if (!empty($rc['high_data_type']) && $rc['high_data_type'] !== $classify_dt) {
                continue;
            }
            if ($rc['low'] !== null && $v < (float)$rc['low']) {
                return 'low';
            }
            if ($rc['high'] !== null && $v > (float)$rc['high']) {
                return 'high';
            }
            return 'okay';
        }
    }
    return 'unknown';
}

/**
 * Pre-compute one status per associated reach so the map and table agree.
 *
 * @param  list<array<string, mixed>>                       $reaches
 * @param  array<int, list<array<string, mixed>>>           $reach_class_thresholds
 * @param  array<string, float>                             $readings_by_dtype
 * @return array<int, string>                               reach_id → status
 */
function _compute_reach_statuses(array $reaches, array $reach_class_thresholds, array $readings_by_dtype): array
{
    $out = [];
    foreach ($reaches as $r) {
        $out[(int)$r['id']] = _classify_reach_status(
            $reach_class_thresholds[(int)$r['id']] ?? [],
            $readings_by_dtype,
        );
    }
    return $out;
}

/**
 * `Cache-Control: no-cache` + include_header with editor-feature context
 * (so the nav's "Comment" link redirects back to this gauge after sign-in).
 *
 * @param array<string, mixed> $gauge
 */
function _render_gauge_header(array $gauge, string $gauge_display): void
{
    header('Cache-Control: no-cache');
    include_header(
        $gauge_display . ' - Gauge',
        '', '', gm_head_links(),
        ['type' => 'gauge', 'id' => (int)$gauge['id']]
    );
}

/**
 * Top nav bar — prev / "Gauge N of M" / next + an inline search form
 * (q only — gauge search has no state filter or hidden toggle).
 *
 * @param int|string                  $position
 * @param int|string                  $total
 * @param array<string, mixed>|false  $prev
 * @param array<string, mixed>|false  $next
 */
function _render_gauge_nav_bar(int|string $position, int|string $total, array|false $prev, array|false $next): void
{
    echo '<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;flex-wrap:wrap">';
    if ($prev) {
        echo '<a href="/gauge.php?id=' . $prev['id'] . '">&laquo; Prev</a>';
    } else {
        echo '<span style="color:#999">&laquo; Prev</span>';
    }
    echo "<span>Gauge $position of $total</span>";
    if ($next) {
        echo '<a href="/gauge.php?id=' . $next['id'] . '">Next &raquo;</a>';
    } else {
        echo '<span style="color:#999">Next &raquo;</span>';
    }
    echo '<form method="get" action="/gauge.php" style="display:flex;gap:.25rem;margin-left:auto">';
    echo '<input type="text" name="q" placeholder="Search gauges…" style="width:14rem">';
    echo '<button type="submit">Go</button>';
    echo '</form>';
    echo '</div>';
}

/**
 * Stale-data banner (yellow) when latest observation is > 7 days old, or
 * an empty-state banner (red) when no observations exist at all. The
 * yellow banner is skipped if observations are fresh.
 *
 * @param list<array<string, mixed>> $readings
 */
function _render_stale_or_empty_banner(array $readings): void
{
    if (!$readings) {
        echo '<p style="padding:.5rem .8rem;background:#fbe8e7;border:1px solid #e53935;border-radius:4px;margin:.5rem 0">'
            . 'No cached observations for this gauge.'
            . '</p>';
        return;
    }
    $latest_ts_all = 0;
    foreach ($readings as $r) {
        if ($r['observed_at']) {
            $t = strtotime((string)$r['observed_at']);
            if ($t > $latest_ts_all) {
                $latest_ts_all = $t;
            }
        }
    }
    $age_days = $latest_ts_all ? (int)floor((time() - $latest_ts_all) / 86400) : null;
    if ($age_days !== null && $age_days > 7) {
        $last = date('Y-m-d', $latest_ts_all);
        echo '<p style="padding:.5rem .8rem;background:#fef6e1;border:1px solid #e8a735;border-radius:4px;margin:.5rem 0">'
            . 'Latest observation was ' . $age_days . ' days ago (' . htmlspecialchars($last) . ').'
            . '</p>';
    }
}

/**
 * 5-col readings table — Type / Value / Time / Change/hr / Status. Same
 * shape as description.php's readings table. Skipped if no readings.
 *
 * @param list<array<string, mixed>> $readings
 */
function _render_gauge_readings_table(array $readings): void
{
    if (!$readings) {
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
        $raw = (float)$r['value'];
        if ($r['data_type'] === 'flow' || $r['data_type'] === 'inflow') {
            $val = number_format($raw, 0) . " $unit";
        } else {
            $val = number_format($raw, 1) . " $unit";
        }
        $time_iso = $r['observed_at'] ? gmdate('Y-m-d\TH:i:s\Z', strtotime($r['observed_at'])) : '';
        $time_display = $r['observed_at'] ? date('m/d H:i', strtotime($r['observed_at'])) : 'N/A';
        $time_html = $time_iso ? "<time datetime=\"$time_iso\">$time_display</time>" : 'N/A';
        $delta_dec = ($r['data_type'] === 'flow' || $r['data_type'] === 'inflow') ? 0 : 2;
        $delta = $r['delta_per_hour'] !== null ? number_format((float)$r['delta_per_hour'], $delta_dec) : '';
        $status = '';
        if ($r['delta_per_hour'] !== null) {
            $dph = (float)$r['delta_per_hour'];
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
 * Date-window selector + the inline SVG plots, when the gauge has any
 * readings. Wraps gp_resolve_window / gp_render_date_form / gp_render_plots.
 *
 * @param array<string, mixed> $gauge
 */
function _render_gauge_date_form_and_plots(
    PDO $db,
    array $gauge,
    int $id,
    string $gauge_display,
    ?string $start_date,
    ?string $end_date,
): void {
    [$latest_ts, $since, $until, $is_default_view] =
        gp_resolve_window($db, (int)$gauge['id'], $start_date, $end_date);
    gp_render_date_form($id, $start_date, $end_date, $latest_ts);
    gp_render_plots($db, (int)$gauge['id'], $gauge_display, $since, $until, $latest_ts, $is_default_view);
}

/**
 * Inline Leaflet map — gauge marker (when coords present) plus a
 * clickable polyline for each associated reach with valid geom. Single-
 * point reaches (geom has no comma) are omitted because they'd render
 * as a degenerate 1-vertex line.
 *
 * Returns the bool gm_render_map returned so the caller knows whether to
 * emit Leaflet `<script>` tags at the end of the page.
 *
 * @param  array<string, mixed>       $gauge
 * @param  list<array<string, mixed>> $reaches
 * @param  array<int, string>         $reach_status_by_id
 */
function _render_gauge_map(array $gauge, array $reaches, array $reach_status_by_id): bool
{
    $reach_tracks_for_map = [];
    foreach ($reaches as $r) {
        if (!empty($r['geom']) && strpos((string)$r['geom'], ',') !== false) {
            $reach_tracks_for_map[] = [
                'id' => (int)$r['id'],
                'name' => (string)$r['name'],
                'location' => (string)($r['description'] ?? ''),
                'classes' => (string)($r['classes'] ?? ''),
                'status' => $reach_status_by_id[(int)$r['id']] ?? 'unknown',
                'geom' => (string)$r['geom'],
            ];
        }
    }
    if ($gauge['latitude'] !== null && $gauge['longitude'] !== null) {
        $glat = number_format((float)$gauge['latitude'], 5, '.', '');
        $glon = number_format((float)$gauge['longitude'], 5, '.', '');
        return gm_render_map(['Gauge' => "$glat,$glon"], null, '#2196F3', $reach_tracks_for_map);
    }
    if ($reach_tracks_for_map) {
        // No gauge coordinates but we still have reach geometry to show.
        return gm_render_map([], null, '#2196F3', $reach_tracks_for_map);
    }
    return false;
}

/**
 * Gauge details table — ID, Name, River, Location, station/agency IDs,
 * Coordinates (Google Maps link), Elevation, Drainage Area, Bank Full,
 * Flood Stage. NWSLI ID is rendered as a link to the NWRFC flowplot page.
 *
 * @param array<string, mixed> $gauge
 */
function _render_gauge_details_table(array $gauge): void
{
    echo '<table class="desc-table">';
    $fields = [
        'ID' => $gauge['id'],
        'Name' => $gauge['name'],
        'River' => $gauge['river'],
        'Location' => $gauge['location'],
        'Station ID' => $gauge['station_id'],
        'USGS ID' => $gauge['usgs_id'],
        'CBTT ID' => $gauge['cbtt_id'],
        'GEOS ID' => $gauge['geos_id'],
        'NWS ID' => $gauge['nws_id'],
        'NWSLI ID' => $gauge['nwsli_id']
            ? '<a href="https://www.nwrfc.noaa.gov/river/station/flowplot/flowplot.cgi?lid='
                . urlencode($gauge['nwsli_id']) . '" target="_blank" rel="noopener">'
                . htmlspecialchars($gauge['nwsli_id']) . '</a>'
            : null,
        'SNOTEL ID' => $gauge['snotel_id'],
    ];

    if ($gauge['latitude'] !== null && $gauge['longitude'] !== null) {
        $lat_f = number_format((float)$gauge['latitude'], 6, '.', '');
        $lon_f = number_format((float)$gauge['longitude'], 6, '.', '');
        $maps_url = "https://www.google.com/maps?q={$lat_f},{$lon_f}";
        $fields['Coordinates'] = "<a href=\"" . htmlspecialchars($maps_url)
            . "\" target=\"_blank\" rel=\"noopener\">{$lat_f}, {$lon_f}</a>";
    }
    if ($gauge['elevation'] !== null) {
        $fields['Elevation'] = number_format((float)$gauge['elevation'], 0) . ' ft';
    }
    if ($gauge['drainage_area'] !== null) {
        $fields['Drainage Area'] = number_format((float)$gauge['drainage_area'], 0) . ' sq mi';
    }
    if ($gauge['bank_full'] !== null) {
        $fields['Bank Full'] = number_format((float)$gauge['bank_full'], 2);
    }
    if ($gauge['flood_stage'] !== null) {
        $fields['Flood Stage'] = number_format((float)$gauge['flood_stage'], 2);
    }

    foreach ($fields as $label => $value) {
        if ($value === null || trim((string)$value) === '') {
            continue;
        }
        if ($label === 'Coordinates' || $label === 'NWSLI ID') {
            echo "<tr><td>$label</td><td>$value</td></tr>\n";
        } else {
            $esc = htmlspecialchars((string)$value);
            echo "<tr><td>$label</td><td>$esc</td></tr>\n";
        }
    }
    echo '</table>';
}

/**
 * Render the "Regression analysis" section when the current gauge is the
 * output of a regression-derived calc gauge (target role), or is consumed
 * as a predictor by one (predictor role). Calc gauges that are themselves
 * predictors get both framings in a single section per slug.
 *
 * The .svg + .json artifacts are produced by
 * scripts/regression/gauge_pair_linear.py and copied into
 * /static/regression/<slug>.{svg,json,html} by the kayak build's
 * _deploy_regression_artifacts(). We sanity-check the slug character
 * class and use is_file() on the static path before emitting any markup
 * — same defense-in-depth pattern as gauge_map.php's
 * basename-whitelist approach.
 *
 * @param list<array<string, mixed>> $sources From _load_gauge_associated().
 */
function _render_gauge_regression(PDO $db, string $gauge_name, array $sources): void
{
    /** @var array<string, array<string, mixed>> $by_slug */
    $by_slug = [];

    // (A) Target role — this gauge is fed by a calc_expression with a
    // provenance_slug. Read directly from $sources to avoid a re-query.
    foreach ($sources as $s) {
        $slug = $s['calc_provenance_slug'] ?? null;
        if (!$slug) {
            continue;
        }
        $by_slug[$slug] = ['slug' => $slug, 'target' => true, 'predictor_for' => []];
    }

    // (B) Predictor role — this gauge's name appears in a regression-derived
    // calc's time_expression. INSTR (not LIKE) because gauge names can
    // contain underscores, which LIKE would treat as single-char wildcards.
    // The substring is bounded by '::' delimiters to avoid matching a
    // shorter name as a prefix of a longer one (the calc-ref convention
    // is `<prefix>::<gauge_name>::<data_type>`, enforced by
    // kayak.cli.calculator._resolve_refs).
    $pred_stmt = $db->prepare(
        'SELECT ce.provenance_slug AS slug,
                g.id AS calc_gauge_id,
                COALESCE(NULLIF(g.display_name, \'\'), g.name) AS calc_gauge_name
         FROM calc_expression ce
         JOIN source s        ON s.calc_expression_id = ce.id
         JOIN gauge_source gs ON gs.source_id = s.id
         JOIN gauge g         ON g.id = gs.gauge_id
         WHERE ce.provenance_slug IS NOT NULL
           AND INSTR(ce.time_expression, :delim) > 0'
    );
    $pred_stmt->execute([':delim' => '::' . $gauge_name . '::']);
    foreach ($pred_stmt->fetchAll() as $row) {
        $slug = (string)$row['slug'];
        if (!isset($by_slug[$slug])) {
            $by_slug[$slug] = ['slug' => $slug, 'target' => false, 'predictor_for' => []];
        }
        $by_slug[$slug]['predictor_for'][] = [
            'gauge_id'   => (int)$row['calc_gauge_id'],
            'gauge_name' => (string)$row['calc_gauge_name'],
        ];
    }

    if (!$by_slug) {
        return;
    }

    $doc_root = $_SERVER['DOCUMENT_ROOT'] ?? '';
    foreach ($by_slug as $entry) {
        $slug = (string)$entry['slug'];
        if (!preg_match('/^[A-Za-z0-9_-]+$/', $slug)) {
            continue;
        }
        $base = $doc_root . '/static/regression/' . $slug;
        $svg_path  = $base . '.svg';
        $json_path = $base . '.json';
        $html_rel  = '/static/regression/' . $slug . '.html';
        if (!is_file($svg_path) || !is_file($json_path)) {
            continue;
        }
        $raw = @file_get_contents($json_path);
        if ($raw === false) {
            continue;
        }
        $fit = json_decode($raw, true, 4);
        if (!is_array($fit) || !isset($fit['coefs']) || !is_array($fit['coefs'])) {
            continue;
        }

        $is_target    = !empty($entry['target']);
        $predictor_of = $entry['predictor_for'] ?? [];

        echo '<section class="regression-analysis">';
        echo '<h3>Regression analysis</h3>';

        // Framing sentence(s) — target / predictor / both.
        $intro_parts = [];
        if ($is_target) {
            $preds = (array)($fit['predictors'] ?? []);
            $pred_label = $preds
                ? 'USGS ' . htmlspecialchars(implode(', USGS ', array_map('strval', $preds)))
                : 'another gauge';
            $intro_parts[] = 'Estimated from ' . $pred_label
                . ' via a fitted linear relationship; the residuals diagnostic below '
                . 'shows fit quality across the predictor flow range.';
        }
        if ($predictor_of) {
            $links = [];
            foreach ($predictor_of as $g) {
                $links[] = '<a href="/gauge.php?id=' . (int)$g['gauge_id'] . '">'
                    . htmlspecialchars($g['gauge_name']) . '</a>';
            }
            $intro_parts[] = 'Used as a predictor for ' . implode(', ', $links) . '.';
        }
        echo '<p>' . implode(' ', $intro_parts) . '</p>';

        $svg_mtime = @filemtime($svg_path) ?: 1;
        echo '<div class="regression-image">';
        echo '<img src="/static/regression/' . htmlspecialchars($slug) . '.svg?v=' . $svg_mtime . '"'
            . ' alt="Residuals (cfs) vs predictor flow for ' . htmlspecialchars($slug) . '"'
            . ' width="600" height="400">';
        echo '</div>';

        echo '<dl class="regression-facts">';
        foreach ($fit['coefs'] as $c) {
            if (!is_array($c)) {
                continue;
            }
            $name  = htmlspecialchars((string)($c['name'] ?? ''));
            $value = (float)($c['value'] ?? 0.0);
            $se    = (float)($c['se'] ?? 0.0);
            $val_s = abs($value) >= 1 ? number_format($value, 4) : sprintf('%.6g', $value);
            $se_s  = abs($se) >= 1 ? number_format($se, 4) : sprintf('%.4g', $se);
            echo '<dt>' . $name . '</dt><dd>' . $val_s . ' ± ' . $se_s . '</dd>';
        }
        $r2    = (float)($fit['r2'] ?? 0.0);
        $rmse  = (float)($fit['rmse'] ?? 0.0);
        $n     = (int)($fit['n'] ?? 0);
        $win   = (array)($fit['window'] ?? []);
        $win_s = count($win) === 2
            ? htmlspecialchars((string)$win[0]) . '..' . htmlspecialchars((string)$win[1])
            : '';
        echo '<dt>r²</dt><dd>' . number_format($r2, 4) . '</dd>';
        echo '<dt>RMSE</dt><dd>' . number_format($rmse, 1) . ' cfs</dd>';
        echo '<dt>n</dt><dd>' . number_format($n) . ' daily means'
            . ($win_s !== '' ? ', ' . $win_s : '') . '</dd>';
        echo '</dl>';

        echo '<p><a href="' . htmlspecialchars($html_rel) . '">Full analysis →</a></p>';
        echo '</section>';
    }
}

/**
 * "Associated Sources" sub-table — one row per source feeding the gauge,
 * with observation count and date of latest reading.
 *
 * @param list<array<string, mixed>> $sources
 */
function _render_associated_sources(array $sources): void
{
    if (!$sources) {
        echo '<p style="margin-top:1rem;color:#666">No associated sources.</p>';
        return;
    }
    echo '<h3 style="margin-top:1rem">Associated Sources</h3>';
    echo '<table class="readings-table">';
    echo '<tr><th>ID</th><th>Name</th><th>Agency</th><th>Observations</th><th>Latest</th></tr>';
    foreach ($sources as $s) {
        $sname = htmlspecialchars($s['name']);
        $sagency = htmlspecialchars($s['agency'] ?? '');
        $cnt = number_format((int)$s['obs_count']);
        $latest = htmlspecialchars($s['latest_at'] ?? '');
        $shref = "/source.php?id={$s['id']}";
        echo "<tr><td><a href=\"$shref\">{$s['id']}</a></td><td><a href=\"$shref\">$sname</a></td><td>$sagency</td><td>$cnt</td><td>$latest</td></tr>\n";
    }
    echo '</table>';
}

/**
 * "Associated Reaches" sub-table — one row per reach linked to this
 * gauge, with class list and a status badge (low/okay/high/unknown).
 *
 * @param list<array<string, mixed>> $reaches
 * @param array<int, string>         $reach_status_by_id
 */
function _render_associated_reaches(array $reaches, array $reach_status_by_id): void
{
    if (!$reaches) {
        echo '<p style="margin-top:1rem;color:#666">No associated reaches.</p>';
        return;
    }
    echo '<h3 style="margin-top:1rem">Associated Reaches</h3>';
    // `assoc-reaches` is the scoping hook for the phone-portrait card
    // layout in style.css — keeps the other four `.readings-table`
    // consumers (gauge readings, sources, description readings,
    // data export) on the default table view. Per
    // docs/done/PLAN_assoc_reaches_card.md.
    echo '<table class="readings-table assoc-reaches">';
    // Location right after Name: on a per-gauge page, r.description differentiates
    // reaches more than r.river (river is often constant across rows). Watershed
    // (r.basin) was the prior 5th column — dropped because it's uniformly the same
    // basin for reaches on the same gauge, so adds no signal. Per
    // docs/done/PLAN_map_and_ui_tweaks.md Item 4.
    echo '<thead><tr><th>Name</th><th>Location</th><th>River</th><th>Class</th><th>Length</th><th>Status</th></tr></thead>';
    echo '<tbody>';
    foreach ($reaches as $r) {
        $rname = htmlspecialchars($r['name']);
        $location = htmlspecialchars((string)($r['description'] ?? ''));
        $river = htmlspecialchars($r['river'] ?? '');
        $classes = htmlspecialchars($r['classes'] ?? '');
        $len = $r['length'] !== null ? number_format((float)$r['length'], 1) . ' mi' : '';
        $status = $reach_status_by_id[(int)$r['id']] ?? 'unknown';
        $status_attr = htmlspecialchars($status);
        $status_html = $status === 'unknown'
            ? '<span style="color:var(--c-text-muted)">unknown</span>'
            : '<span class="level-' . $status . '">' . $status . '</span>';
        $rhref = "/description.php?id={$r['id']}";
        echo "<tr data-status=\"$status_attr\">"
            . "<td class=\"td-name\" data-label=\"Name\"><a href=\"$rhref\">$rname</a></td>"
            . "<td data-label=\"Location\"><a href=\"$rhref\">$location</a></td>"
            . "<td class=\"secondary\" data-label=\"River\">$river</td>"
            . "<td data-label=\"Class\">$classes</td>"
            . "<td data-label=\"Length\">$len</td>"
            . "<td class=\"td-status\" data-label=\"Status\">$status_html</td>"
            . "</tr>\n";
    }
    echo '</tbody></table>';
}

/**
 * Footer button bar — Back, All gauges, plus an Edit button when an
 * editor with maintainer role is signed in. Gauge proposals aren't yet
 * supported by propose.php, so non-maintainer editors see nothing extra.
 *
 * Also emits the Leaflet + feature-map script tags when the map rendered.
 */
function _render_gauge_footer(int $id, bool $has_map): void
{
    $btn_style = 'display:inline-flex;align-items:center;min-height:44px;padding:8px 12px';
    echo '<nav style="margin-top:1rem;display:flex;flex-wrap:wrap;gap:.5rem">';
    echo '<a href="/index.html" style="' . $btn_style . '">Back to main page</a>';
    echo '<a href="/gauges.html" style="' . $btn_style . '">All gauges</a>';
    if (editor_feature_enabled()) {
        $editor = current_editor();
        if (is_maintainer($editor)) {
            echo '<a href="/edit.php?id=' . $id . '&amp;type=gauge" style="' . $btn_style . '">Edit</a>';
        }
    }
    echo '</nav>';

    if ($has_map) {
        $fm_mtime = @filemtime($_SERVER['DOCUMENT_ROOT'] . '/static/feature-map.js') ?: 1;
        echo '<script src="/static/leaflet.js" defer></script>';
        echo '<script src="/static/feature-map.js?v=' . $fm_mtime . '" defer></script>';
    }
}
