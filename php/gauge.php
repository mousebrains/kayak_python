<?php
declare(strict_types=1);
/**
 * Gauge browser — view gauge details with associated sources and reaches.
 *
 * Usage: /gauge.php?id=<gauge_id> or /gauge.php?q=<search>
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/gauge_plots.php';
require_once __DIR__ . '/includes/gauge_map.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/validate.php';

$db = get_db();

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$q  = filter_input(INPUT_GET, 'q', FILTER_DEFAULT);
$start_date = validate_date(filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS));
$end_date   = validate_date(filter_input(INPUT_GET, 'end',   FILTER_SANITIZE_SPECIAL_CHARS));
$has_map = false;

// --- Search mode ---
if ($q !== null && $q !== '') {
    $q = trim($q);
    $stmt = $db->prepare(
        'SELECT id, name, location FROM gauge
         WHERE name LIKE ? OR location LIKE ? OR station_id LIKE ?
            OR usgs_id LIKE ? OR cbtt_id LIKE ? OR geos_id LIKE ?
            OR nws_id LIKE ? OR nwsli_id LIKE ? OR snotel_id LIKE ?
         ORDER BY id'
    );
    $pat = "%$q%";
    $stmt->execute([$pat, $pat, $pat, $pat, $pat, $pat, $pat, $pat, $pat]);
    $results = $stmt->fetchAll();

    if (count($results) === 1) {
        header('Location: /gauge.php?id=' . $results[0]['id']);
        exit;
    }

    header('Cache-Control: no-cache');
    include_header('Gauge Search', '', '', '', ['picker_kind' => 'gauge']);
    echo '<h2>Gauge Search</h2>';

    if (!$results) {
        echo '<p>No gauges matching &ldquo;' . htmlspecialchars($q) . '&rdquo;.</p>';
    } else {
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

    echo '<p style="margin-top:1rem"><a href="/gauge.php">Browse all gauges</a></p>';
    include_footer();
    exit;
}

// --- Default: show first gauge ---
if (!$id) {
    $row = $db->query('SELECT id FROM gauge ORDER BY id ASC LIMIT 1')->fetch();
    if (!$row) {
        header('Cache-Control: no-cache');
        include_header('Gauges', '', '', '', ['picker_kind' => 'gauge']);
        echo '<p>No gauges in database.</p>';
        include_footer();
        exit;
    }
    $id = $row['id'];
}

// --- Load current gauge ---
$stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
$stmt->execute([$id]);
$gauge = $stmt->fetch();
if (!$gauge) { http_response_code(404); exit('Gauge not found'); }

// --- Navigation ---
$prev_stmt = $db->prepare('SELECT id FROM gauge WHERE id < ? ORDER BY id DESC LIMIT 1');
$prev_stmt->execute([$id]);
$prev = $prev_stmt->fetch();

$next_stmt = $db->prepare('SELECT id FROM gauge WHERE id > ? ORDER BY id ASC LIMIT 1');
$next_stmt->execute([$id]);
$next = $next_stmt->fetch();

$total = $db->query('SELECT COUNT(*) FROM gauge')->fetchColumn();
$pos = $db->prepare('SELECT COUNT(*) FROM gauge WHERE id <= ?');
$pos->execute([$id]);
$position = $pos->fetchColumn();

// --- Associated sources ---
$sources_stmt = $db->prepare(
    'SELECT s.id, s.name, s.agency,
            (SELECT COUNT(*) FROM observation o WHERE o.source_id = s.id) AS obs_count,
            (SELECT SUBSTR(MAX(o.observed_at), 1, 10) FROM observation o WHERE o.source_id = s.id) AS latest_at
     FROM source s
     JOIN gauge_source gs ON s.id = gs.source_id
     WHERE gs.gauge_id = ?
     ORDER BY s.name'
);
$sources_stmt->execute([$id]);
$sources = $sources_stmt->fetchAll();

// --- Associated reaches ---
// Pull class names from reach_class (the canonical source) rather than the
// rarely-populated reach.difficulties column.
$reaches_stmt = $db->prepare(
    'SELECT r.id, COALESCE(NULLIF(r.display_name, \'\'), r.name) AS name, r.river, r.length, r.basin, r.geom, r.description,
            (SELECT GROUP_CONCAT(rc.name, \', \') FROM reach_class rc WHERE rc.reach_id = r.id) AS classes
     FROM reach r WHERE r.gauge_id = ? ORDER BY r.sort_name'
);
$reaches_stmt->execute([$id]);
$reaches = $reaches_stmt->fetchAll();

// Per-reach low/high thresholds for classifying the gauge's current readings
// against each reach's runnable range. Pulled in one shot to avoid N+1.
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
    foreach ($thr_stmt->fetchAll() as $row) {
        $reach_class_thresholds[(int)$row['reach_id']][] = $row;
    }
}

// Prefer the normalized display_name populated by
// scripts/seed_gauge_display.py; fall back to the internal canonical
// `name` when the row predates the seeder (e.g. a freshly inserted gauge).
$gauge_display = $gauge['display_name'] ?: $gauge['name'];

// --- Render ---
header('Cache-Control: no-cache');
include_header(
    $gauge_display . ' - Gauge',
    '', '', '',
    ['type' => 'gauge', 'id' => (int)$gauge['id']]
);

// Navigation bar
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

echo '<h2>' . htmlspecialchars($gauge_display) . '</h2>';

// --- Current readings + stale banner ---
$readings_stmt = $db->prepare(
    'SELECT data_type, value, observed_at, delta_per_hour
     FROM latest_gauge_observation WHERE gauge_id = ?'
);
$readings_stmt->execute([(int)$gauge['id']]);
$readings = $readings_stmt->fetchAll();

// dtype → numeric value map for downstream classification of associated reaches.
$readings_by_dtype = [];
foreach ($readings as $r) {
    if ($r['value'] !== null) {
        $readings_by_dtype[(string)$r['data_type']] = (float)$r['value'];
    }
}

// Mirrors db/reaches.py::classify_level + the priority order in
// build.py::_get_row_data — try (flow, gauge, inflow-as-flow) and pick the
// first dtype with both a reading on this gauge and a class threshold whose
// data_type matches. Returns 'low' | 'okay' | 'high' | 'unknown'.
$classify_reach_status = static function (array $thresholds, array $readings_by_dtype): string {
    $candidates = [['flow', 'flow'], ['gauge', 'gauge'], ['inflow', 'flow']];
    foreach ($candidates as [$reading_dt, $classify_dt]) {
        if (!isset($readings_by_dtype[$reading_dt])) continue;
        $v = $readings_by_dtype[$reading_dt];
        foreach ($thresholds as $rc) {
            if ($rc['low'] === null && $rc['high'] === null) continue;
            if (!empty($rc['low_data_type']) && $rc['low_data_type'] !== $classify_dt) continue;
            if (!empty($rc['high_data_type']) && $rc['high_data_type'] !== $classify_dt) continue;
            if ($rc['low'] !== null && $v < (float)$rc['low']) return 'low';
            if ($rc['high'] !== null && $v > (float)$rc['high']) return 'high';
            return 'okay';
        }
    }
    return 'unknown';
};

// Pre-compute one status per associated reach so the map and table agree.
$reach_status_by_id = [];
foreach ($reaches as $r) {
    $reach_status_by_id[(int)$r['id']] = $classify_reach_status(
        $reach_class_thresholds[(int)$r['id']] ?? [],
        $readings_by_dtype
    );
}

if ($readings) {
    $latest_ts_all = 0;
    foreach ($readings as $r) {
        if ($r['observed_at']) {
            $t = strtotime((string)$r['observed_at']);
            if ($t > $latest_ts_all) $latest_ts_all = $t;
        }
    }
    $age_days = $latest_ts_all ? (int)floor((time() - $latest_ts_all) / 86400) : null;
    if ($age_days !== null && $age_days > 7) {
        $last = date('Y-m-d', $latest_ts_all);
        echo '<p style="padding:.5rem .8rem;background:#fef6e1;border:1px solid #e8a735;border-radius:4px;margin:.5rem 0">'
           . 'Latest observation was ' . $age_days . ' days ago (' . htmlspecialchars($last) . ').'
           . '</p>';
    }
} else {
    echo '<p style="padding:.5rem .8rem;background:#fbe8e7;border:1px solid #e53935;border-radius:4px;margin:.5rem 0">'
       . 'No cached observations for this gauge.'
       . '</p>';
}

if ($readings) {
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
        $time_iso = $r['observed_at'] ? date('Y-m-d\TH:i:s\Z', strtotime($r['observed_at'])) : '';
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

// --- Date range selector + plots ---
if ($readings) {
    [$latest_ts, $since, $until, $is_default_view] =
        gp_resolve_window($db, (int)$gauge['id'], $start_date, $end_date);
    gp_render_date_form($id, $start_date, $end_date, $latest_ts);
    gp_render_plots($db, (int)$gauge['id'], $gauge_display, $since, $until, $latest_ts, $is_default_view);
}

// --- Map (gauge marker + clickable polylines for each associated reach) ---
$reach_tracks_for_map = [];
foreach ($reaches as $r) {
    // Only LineString geoms become tracks; single-point reaches are
    // omitted (they'd render as a 1-vertex degenerate line).
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
    $has_map = gm_render_map(['Gauge' => "$glat,$glon"], null, '#2196F3', $reach_tracks_for_map);
} elseif ($reach_tracks_for_map) {
    // No gauge coordinates but we still have reach geometry to show.
    $has_map = gm_render_map([], null, '#2196F3', $reach_tracks_for_map);
}

// --- Gauge details (moved below the map so the flow info is the page lead) ---
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
    'NWSLI ID' => $gauge['nwsli_id'] ? '<a href="https://www.nwrfc.noaa.gov/river/station/flowplot/flowplot.cgi?lid=' . urlencode($gauge['nwsli_id']) . '" target="_blank" rel="noopener">' . htmlspecialchars($gauge['nwsli_id']) . '</a>' : null,
    'SNOTEL ID' => $gauge['snotel_id'],
];

// Coordinates with Google Maps link
$lat = $gauge['latitude'];
$lon = $gauge['longitude'];
if ($lat !== null && $lon !== null) {
    $lat_f = number_format((float)$lat, 6, '.', '');
    $lon_f = number_format((float)$lon, 6, '.', '');
    $maps_url = "https://www.google.com/maps?q={$lat_f},{$lon_f}";
    $fields['Coordinates'] = "<a href=\"" . htmlspecialchars($maps_url) . "\" target=\"_blank\" rel=\"noopener\">{$lat_f}, {$lon_f}</a>";
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
    if ($value === null || trim((string)$value) === '') continue;
    if ($label === 'Coordinates' || $label === 'NWSLI ID') {
        echo "<tr><td>$label</td><td>$value</td></tr>\n";
    } else {
        $esc = htmlspecialchars((string)$value);
        echo "<tr><td>$label</td><td>$esc</td></tr>\n";
    }
}

echo '</table>';

// Associated sources
if ($sources) {
    echo '<h3 style="margin-top:1rem">Associated Sources</h3>';
    echo '<table class="readings-table">';
    echo '<tr><th>ID</th><th>Name</th><th>Agency</th><th>Observations</th><th>Latest</th></tr>';
    foreach ($sources as $s) {
        $sname = htmlspecialchars($s['name']);
        $sagency = htmlspecialchars($s['agency'] ?? '');
        $cnt = number_format((int)$s['obs_count']);
        $latest = htmlspecialchars($s['latest_at'] ?? '');
        echo "<tr><td>{$s['id']}</td><td><a href=\"/source.php?id={$s['id']}\">$sname</a></td><td>$sagency</td><td>$cnt</td><td>$latest</td></tr>\n";
    }
    echo '</table>';
} else {
    echo '<p style="margin-top:1rem;color:#666">No associated sources.</p>';
}

// Associated reaches
if ($reaches) {
    echo '<h3 style="margin-top:1rem">Associated Reaches</h3>';
    echo '<table class="readings-table">';
    echo '<tr><th>Name</th><th>River</th><th>Class</th><th>Length</th><th>Watershed</th><th>Status</th></tr>';
    foreach ($reaches as $r) {
        $rname = htmlspecialchars($r['name']);
        $river = htmlspecialchars($r['river'] ?? '');
        $classes = htmlspecialchars($r['classes'] ?? '');
        $len = $r['length'] !== null ? number_format((float)$r['length'], 1) . ' mi' : '';
        $basin = htmlspecialchars($r['basin'] ?? '');
        $status = $reach_status_by_id[(int)$r['id']] ?? 'unknown';
        $status_html = $status === 'unknown'
            ? '<span style="color:var(--c-text-muted)">unknown</span>'
            : '<span class="level-' . $status . '">' . $status . '</span>';
        echo "<tr><td><a href=\"/description.php?id={$r['id']}\">$rname</a></td><td>$river</td><td>$classes</td><td>$len</td><td>$basin</td><td>$status_html</td></tr>\n";
    }
    echo '</table>';
} else {
    echo '<p style="margin-top:1rem;color:#666">No associated reaches.</p>';
}

// Editor affordances (gauge-only for now — gauge proposals not yet supported
// by propose.php, so only maintainers see an Edit button).
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
    echo '<script src="/static/leaflet.js" defer></script>';
    echo '<script src="/static/feature-map.js" defer></script>';
}

include_footer();
