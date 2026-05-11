<?php
declare(strict_types=1);
/**
 * Reach browser — view reach details with navigation.
 *
 * Usage: /reach.php?id=<reach_id> or /reach.php?q=<search>
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/html.php';

$db = get_db();
$has_map = false;
$map_scripts = '';

// Compact layout — desktop utility page; overrides global touch-target sizes.
$compact_css = '<style>'
    . '.desc-table td{padding:2px 6px}'
    . '.desc-table a{display:inline;min-height:0;line-height:normal}'
    . '.desc-table{font-size:.9rem;margin-bottom:.25rem}'
    . 'h2{margin:.25rem 0 .35rem;font-size:1.25rem}'
    . 'h3{margin:.6rem 0 .2rem;font-size:1rem}'
    . 'main{padding:.25rem .5rem}'
    . '#reach-map{height:320px !important;margin-top:.5rem !important}'
    . '#search-map{height:65vh !important;min-height:480px !important;margin-top:.5rem !important}'
    . '</style>';

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$q  = filter_input(INPUT_GET, 'q', FILTER_DEFAULT);
$st = filter_input(INPUT_GET, 'st', FILTER_DEFAULT);
$st = ($st !== null && $st !== '') ? strtoupper(trim($st)) : '';
$hidden = filter_input(INPUT_GET, 'hidden', FILTER_VALIDATE_INT);
$hidden = ($hidden === 1) ? 1 : 0;

// --- Search mode ---
$q_trimmed = ($q !== null && $q !== '') ? trim($q) : '';
if ($q_trimmed !== '' || $st !== '') {
    $q = $q_trimmed;
    if ($q !== '' && $st !== '') {
        $pat = "%$q%";
        $stmt = $db->prepare(
            'SELECT r.id, COALESCE(NULLIF(r.display_name, \'\'), r.name) AS name, r.river,
                    r.description, r.gauge_id, r.latitude_start, r.longitude_start,
                    r.latitude_end, r.longitude_end, r.latitude, r.longitude,
                    r.sort_name, r.aw_id, r.geom
             FROM reach r
             JOIN reach_state rs ON rs.reach_id = r.id
             JOIN state s ON s.id = rs.state_id
             WHERE (r.display_name LIKE ? OR r.name LIKE ? OR r.river LIKE ?)
               AND s.abbreviation = ?
               AND r.no_show = ?
             ORDER BY r.sort_name
             LIMIT 200'
        );
        $stmt->execute([$pat, $pat, $pat, $st, $hidden]);
    } elseif ($q !== '') {
        $pat = "%$q%";
        $stmt = $db->prepare(
            'SELECT r.id, COALESCE(NULLIF(r.display_name, \'\'), r.name) AS name, r.river,
                    r.description, r.gauge_id, r.latitude_start, r.longitude_start,
                    r.latitude_end, r.longitude_end, r.latitude, r.longitude,
                    r.sort_name, r.aw_id, r.geom
             FROM reach r
             WHERE (r.display_name LIKE ? OR r.name LIKE ? OR r.river LIKE ?)
               AND r.no_show = ?
             ORDER BY r.sort_name
             LIMIT 200'
        );
        $stmt->execute([$pat, $pat, $pat, $hidden]);
    } else {
        // State filter only, no text search.
        // Higher cap than the text-search variants because a state listing
        // is the user's expected "all reaches in OR" page; large states
        // (Oregon, California) can legitimately exceed 200.
        $stmt = $db->prepare(
            'SELECT r.id, COALESCE(NULLIF(r.display_name, \'\'), r.name) AS name, r.river,
                    r.description, r.gauge_id, r.latitude_start, r.longitude_start,
                    r.latitude_end, r.longitude_end, r.latitude, r.longitude,
                    r.sort_name, r.aw_id, r.geom
             FROM reach r
             JOIN reach_state rs ON rs.reach_id = r.id
             JOIN state s ON s.id = rs.state_id
             WHERE s.abbreviation = ?
               AND r.no_show = ?
             ORDER BY r.sort_name
             LIMIT 1000'
        );
        $stmt->execute([$st, $hidden]);
    }
    $results = $stmt->fetchAll();

    if (count($results) === 1) {
        header('Location: /reach.php?id=' . $results[0]['id']);
        exit;
    }

    // Collect latest flow/gage/inflow readings for all result reaches
    $reach_readings = [];
    if ($results) {
        $gauge_ids = array_values(array_unique(array_filter(array_column($results, 'gauge_id'))));
        if ($gauge_ids) {
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
        }
    }

    // Collect classes and guidebook abbreviations for all result reaches
    $reach_ids = array_column($results, 'id');
    $reach_classes = [];
    $reach_guides = [];
    if ($reach_ids) {
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
        // Soggy Sneakers edition number by guidebook id
        $ss_edition = [9 => 1, 2 => 3, 3 => 4, 4 => 5];
        // Non-SS guidebook abbreviation map
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
        // and prepend so SS appears before non-SS guides
        foreach ($reach_ss as $rid => $editions) {
            rsort($editions);
            $ss_label = 'SS' . implode('', $editions);
            $reach_guides[$rid] = [$ss_label => true] + ($reach_guides[$rid] ?? []);
        }

        // Add AW for reaches with aw_id set (even without a guidebook row)
        foreach ($results as $r) {
            if (!empty($r['aw_id'])) {
                $reach_guides[$r['id']]['AW'] = true;
            }
        }
    }

    header('Cache-Control: no-cache');
    $preconnects = '<link rel="preconnect" href="https://a.tile.opentopomap.org">'
        . '<link rel="preconnect" href="https://b.tile.opentopomap.org">'
        . '<link rel="preconnect" href="https://c.tile.opentopomap.org">';
    include_header('Reach Search', '', '', $preconnects);
    echo $compact_css;
    echo '<h2>Reach Search</h2>';

    if (!$results) {
        $label = $q !== '' ? '&ldquo;' . htmlspecialchars($q) . '&rdquo;' : htmlspecialchars($st);
        echo '<p>No reaches matching ' . $label . '.</p>';
    } else {
        // Map with reach locations
        $map_reaches = [];
        foreach ($results as $idx => $r) {
            $lat = $r['latitude'] ?? $r['latitude_start'] ?? null;
            $lon = $r['longitude'] ?? $r['longitude_start'] ?? null;
            if ($lat !== null && $lon !== null) {
                $track = null;
                if (!empty($r['geom'])) {
                    $track = [];
                    foreach (explode(',', $r['geom']) as $pair) {
                        $parts = preg_split('/\s+/', trim($pair));
                        if (count($parts) === 2) {
                            $track[] = [(float)$parts[1], (float)$parts[0]];
                        }
                    }
                    // Downsample to ~100 points for search map
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
        }

        $colors = ['#e6194b','#3cb44b','#4363d8','#f58231','#911eb4',
                    '#42d4f4','#f032e6','#bfef45','#469990','#dcbeff',
                    '#9A6324','#800000','#aaffc3','#808000','#000075'];

        $label = $q !== '' ? '&ldquo;' . htmlspecialchars($q) . '&rdquo;' : htmlspecialchars($st);
        echo '<p>' . count($results) . ' reaches matching ' . $label . ':</p>';
        echo '<table class="desc-table">';
        echo '<tr><th>ID</th><th>Name</th><th>Description</th><th>Class</th><th>Sort Name</th><th>Guides</th><th>Flow / Gage</th></tr>';
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
            $color = $colors[$idx % count($colors)];
            $swatch = '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' . $color . ';margin-right:4px" title="Map marker color"></span>';
            $cls = htmlspecialchars(implode(', ', $reach_classes[$r['id']] ?? []));
            $guides = implode(', ', array_keys($reach_guides[$r['id']] ?? []));
            echo "<tr><td>{$r['id']}</td><td>{$swatch}<a href=\"/reach.php?id={$r['id']}\">$rname</a></td><td>$desc</td><td>$cls</td><td>$sname</td><td>$guides</td><td>$reading</td></tr>\n";
        }
        echo '</table>';

        // Collect unique gauges with locations for map
        $map_gauges = [];
        if ($gauge_ids) {
            $ph = implode(',', array_fill(0, count($gauge_ids), '?'));
            $g_stmt = $db->prepare(
                "SELECT id, name, latitude, longitude FROM gauge WHERE id IN ($ph) AND latitude IS NOT NULL AND longitude IS NOT NULL"
            );
            $g_stmt->execute($gauge_ids);
            foreach ($g_stmt->fetchAll() as $g) {
                $glabel = $g['name'];
                if (isset($reach_readings[$g['id']])) {
                    $parts = [];
                    $rr = $reach_readings[$g['id']];
                    if (isset($rr['flow'])) $parts[] = number_format((float)$rr['flow']['value'], 0) . ' cfs';
                    if (isset($rr['gauge'])) $parts[] = number_format((float)$rr['gauge']['value'], 2) . ' ft';
                    if ($parts) $glabel .= ' (' . implode(' / ', $parts) . ')';
                }
                $map_gauges[] = [
                    'name' => $glabel,
                    'lat' => (float)$g['latitude'],
                    'lon' => (float)$g['longitude'],
                ];
            }
        }

        if ($map_reaches) {
            $leaflet_css = file_get_contents($_SERVER['DOCUMENT_ROOT'] . '/static/leaflet.css');
            echo '<style>' . $leaflet_css . '</style>';
            $map_json = htmlspecialchars(json_encode($map_reaches));
            $colors_json = htmlspecialchars(json_encode($colors));
            $gauges_json = htmlspecialchars(json_encode($map_gauges));
            echo '<div id="search-map" style="height:65vh;min-height:480px;margin-top:1rem;border:1px solid #ccc" data-reaches="' . $map_json . '" data-colors="' . $colors_json . '" data-gauges="' . $gauges_json . '"></div>';
            $has_map = true;
            $map_scripts = '<script src="/static/leaflet.js" defer></script><script src="/static/search-map.js" defer></script>';
        }

    }

    echo '<p style="margin-top:1rem"><a href="/reach.php">Browse all reaches</a></p>';
    if ($has_map) echo $map_scripts;
    include_footer();
    exit;
}

// --- Default: show first reach ---
if (!$id) {
    $row = $db->prepare('SELECT id FROM reach WHERE no_show = ? ORDER BY sort_name, id ASC LIMIT 1');
    $row->execute([$hidden]);
    $row = $row->fetch();
    if (!$row) {
        header('Cache-Control: no-cache');
        include_header('Reaches');
        echo '<p>No reaches in database.</p>';
        include_footer();
        exit;
    }
    $id = $row['id'];
}

// --- Load current reach ---
$stmt = $db->prepare('SELECT * FROM reach WHERE id = ?');
$stmt->execute([$id]);
$reach = $stmt->fetch();
if (!$reach) { http_response_code(404); exit('Reach not found'); }

$name = $reach['display_name'] ?: $reach['name'];

// --- Navigation ---
$prev_stmt = $db->prepare('SELECT id FROM reach WHERE (sort_name < ? OR (sort_name = ? AND id < ?)) AND no_show = ? ORDER BY sort_name DESC, id DESC LIMIT 1');
$prev_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
$prev = $prev_stmt->fetch();

$next_stmt = $db->prepare('SELECT id FROM reach WHERE (sort_name > ? OR (sort_name = ? AND id > ?)) AND no_show = ? ORDER BY sort_name ASC, id ASC LIMIT 1');
$next_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
$next = $next_stmt->fetch();

$total_stmt = $db->prepare('SELECT COUNT(*) FROM reach WHERE no_show = ?');
$total_stmt->execute([$hidden]);
$total = $total_stmt->fetchColumn();
$pos = $db->prepare('SELECT COUNT(*) FROM reach WHERE (sort_name < ? OR (sort_name = ? AND id <= ?)) AND no_show = ?');
$pos->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
$position = $pos->fetchColumn();

// --- Load related data ---
$gauge = null;
if ($reach['gauge_id']) {
    $stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
    $stmt->execute([$reach['gauge_id']]);
    $gauge = $stmt->fetch();
}

$states_stmt = $db->prepare(
    'SELECT s.name FROM state s JOIN reach_state rs ON s.id = rs.state_id WHERE rs.reach_id = ?'
);
$states_stmt->execute([$id]);
$states = array_column($states_stmt->fetchAll(), 'name');

$classes_stmt = $db->prepare('SELECT * FROM reach_class WHERE reach_id = ?');
$classes_stmt->execute([$id]);
$classes = $classes_stmt->fetchAll();

// Derive low/okay/high bands from the reach's primary class range (the
// first reach_class row with populated bounds). Provides the same array
// shape the renderer used when reach_level was the source of truth.
$class_range_stmt = $db->prepare(
    'SELECT low, low_data_type, high, high_data_type
     FROM reach_class
     WHERE reach_id = ? AND (low IS NOT NULL OR high IS NOT NULL)
     ORDER BY id LIMIT 1'
);
$class_range_stmt->execute([$id]);
$class_range = $class_range_stmt->fetch();
$flow_levels = [];
if ($class_range) {
    $lo = $class_range['low'];
    $hi = $class_range['high'];
    $lo_dt = $class_range['low_data_type'] ?: 'flow';
    $hi_dt = $class_range['high_data_type'] ?: 'flow';
    $flow_levels = [
        ['level' => 'low',  'low' => null, 'low_data_type' => $lo_dt, 'high' => $lo,   'high_data_type' => $lo_dt],
        ['level' => 'okay', 'low' => $lo,  'low_data_type' => $lo_dt, 'high' => $hi,   'high_data_type' => $hi_dt],
        ['level' => 'high', 'low' => $hi,  'low_data_type' => $hi_dt, 'high' => null,  'high_data_type' => $hi_dt],
    ];
}

$gb_stmt = $db->prepare(
    'SELECT g.title, g.subtitle, g.edition, g.author, g.url AS book_url,
            rg.page, rg.run, rg.url AS entry_url
     FROM reach_guidebook rg
     JOIN guidebook g ON g.id = rg.guidebook_id
     WHERE rg.reach_id = ?
     ORDER BY g.sort_order, g.title, g.edition'
);
$gb_stmt->execute([$id]);
$guidebooks = $gb_stmt->fetchAll();

// --- Render ---
header('Cache-Control: no-cache');
$preconnects = '<link rel="preconnect" href="https://a.tile.opentopomap.org">'
    . '<link rel="preconnect" href="https://b.tile.opentopomap.org">'
    . '<link rel="preconnect" href="https://c.tile.opentopomap.org">';
include_header($name . ' - Reach', '', '', $preconnects);
echo $compact_css;

// Navigation bar
echo '<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;flex-wrap:wrap">';
$hq = $hidden ? '&amp;hidden=1' : '';
if ($prev) {
    echo '<a href="/reach.php?id=' . $prev['id'] . $hq . '">&laquo; Prev</a>';
} else {
    echo '<span style="color:#999">&laquo; Prev</span>';
}
echo "<span>Reach $position of $total</span>";
if ($next) {
    echo '<a href="/reach.php?id=' . $next['id'] . $hq . '">Next &raquo;</a>';
} else {
    echo '<span style="color:#999">Next &raquo;</span>';
}
$all_states = $db->query('SELECT abbreviation FROM state ORDER BY abbreviation')->fetchAll(PDO::FETCH_COLUMN);
echo '<form method="get" action="/reach.php" style="display:flex;gap:.25rem;margin-left:auto">';
echo '<input type="text" name="q" placeholder="Search reaches…" style="width:14rem" value="' . htmlspecialchars($q ?? '') . '">';
echo '<label for="st" class="sr-only">State</label>';
echo '<select name="st" id="st"><option value="">All states</option>';
foreach ($all_states as $s) {
    $sel = ($st === $s) ? ' selected' : '';
    $esc = htmlspecialchars($s);
    echo "<option value=\"$esc\"$sel>$esc</option>";
}
echo '</select>';
if ($hidden) echo '<input type="hidden" name="hidden" value="1">';
echo '<button type="submit">Go</button>';
echo '</form>';
$toggle_hidden = $hidden ? 0 : 1;
$toggle_label = $hidden ? 'Show visible' : 'Show hidden';
echo "<a href=\"/reach.php?id=$id&amp;hidden=$toggle_hidden\">$toggle_label</a>";
echo '</div>';

// Title linked to description
echo '<h2><a href="/description.php?id=' . $id . '">' . htmlspecialchars($name) . '</a></h2>';

// Main details table
echo '<table class="desc-table">';

$fields = [
    'ID' => $reach['id'],
    'Name' => $reach['name'],
    'Display Name' => $reach['display_name'],
    'River' => $reach['river'],
    'State' => implode(', ', $states),
    'Class' => implode(', ', array_column($classes, 'name')),
    'Watershed' => $reach['basin'],
    'Watershed Area' => $reach['basin_area'] ? number_format((float)$reach['basin_area'], 1) . ' sq mi' : null,
    'Region' => $reach['region'],
    'Season' => $reach['season'],
    'Nature' => $reach['nature'],
    'Watershed type' => $reach['watershed_type'],
    'Scenery' => $reach['scenery'],
    'Remoteness' => $reach['remoteness'],
    'Features' => $reach['features'],
    'Length' => $reach['length'] ? number_format((float)$reach['length'], 1) . ' mi' : null,
    'Gradient' => $reach['gradient'] ? number_format((float)$reach['gradient'], 0) . ' ft/mi' : null,
    'Max Gradient' => $reach['max_gradient'] ? number_format((float)$reach['max_gradient'], 0) . ' ft/mi' : null,
    'Elevation' => $reach['elevation'] ? number_format((float)$reach['elevation'], 0) . ' ft' : null,
    'Elevation Lost' => $reach['elevation_lost'] ? number_format((float)$reach['elevation_lost'], 0) . ' ft' : null,
    'Optimal Flow' => $reach['optimal_flow'] ? number_format((float)$reach['optimal_flow'], 0) . ' CFS' : null,
    'No Show' => $reach['no_show'] ? 'Yes' : null,
    'Updated' => $reach['updated_at'],
];

// Coordinate fields as Google Maps links
$coord_pairs = [];
if ($reach['latitude_start'] !== null && $reach['longitude_start'] !== null) {
    $lat = number_format((float)$reach['latitude_start'], 6, '.', '');
    $lon = number_format((float)$reach['longitude_start'], 6, '.', '');
    $url = "https://www.google.com/maps?q=$lat,$lon";
    $fields['Put-in'] = "<a href=\"" . htmlspecialchars($url) . "\" target=\"_blank\" rel=\"noopener\">$lat, $lon</a>";
}
if ($reach['latitude_end'] !== null && $reach['longitude_end'] !== null) {
    $lat = number_format((float)$reach['latitude_end'], 6, '.', '');
    $lon = number_format((float)$reach['longitude_end'], 6, '.', '');
    $url = "https://www.google.com/maps?q=$lat,$lon";
    $fields['Take-out'] = "<a href=\"" . htmlspecialchars($url) . "\" target=\"_blank\" rel=\"noopener\">$lat, $lon</a>";
}

$fields += [
    'Description' => $reach['description'],
    'Difficulties' => $reach['difficulties'],
    'Notes' => $reach['notes'],
];

$html_fields = ['Put-in', 'Take-out', 'AW ID'];
$autolink_fields = ['Description', 'Notes'];
foreach ($fields as $label => $value) {
    if ($value === null || trim((string)$value) === '') continue;
    if (in_array($label, $html_fields)) {
        echo "<tr><td>$label</td><td>$value</td></tr>\n";
    } elseif (in_array($label, $autolink_fields)) {
        echo "<tr><td>$label</td><td>" . nl2br(autolink_urls((string)$value)) . "</td></tr>\n";
    } else {
        $esc = htmlspecialchars((string)$value);
        echo "<tr><td>$label</td><td>$esc</td></tr>\n";
    }
}
echo '</table>';

// Classes with ranges
if ($classes) {
    $has_ranges = false;
    foreach ($classes as $c) {
        if ($c['low'] !== null || $c['high'] !== null) { $has_ranges = true; break; }
    }
    if ($has_ranges) {
        echo '<h3 style="margin-top:1rem">Class Ranges</h3>';
        echo '<table class="desc-table">';
        echo '<tr><th>Class</th><th>Low</th><th>High</th></tr>';
        foreach ($classes as $c) {
            $cname = htmlspecialchars($c['name']);
            $lo = $c['low'] !== null ? number_format((float)$c['low'], 1) : '';
            $hi = $c['high'] !== null ? number_format((float)$c['high'], 1) : '';
            if ($c['low_data_type']) $lo .= ' ' . htmlspecialchars($c['low_data_type']);
            if ($c['high_data_type']) $hi .= ' ' . htmlspecialchars($c['high_data_type']);
            echo "<tr><td>$cname</td><td>$lo</td><td>$hi</td></tr>\n";
        }
        echo '</table>';
    }
}

// Flow levels — 2-row table with Low, Okay, High as columns
if ($flow_levels) {
    $by_level = [];
    foreach ($flow_levels as $fl) {
        $by_level[$fl['level']] = $fl;
    }
    echo '<h3 style="margin-top:1rem">Flow Levels</h3>';
    echo '<table class="desc-table">';
    echo '<tr><th style="text-align:center">Low</th><th style="text-align:center">Okay</th><th style="text-align:center">High</th></tr>';
    $cells = [];
    foreach (['low', 'okay', 'high'] as $lvl) {
        $parts = [];
        // $by_level always carries every key of $flow_levels, which is
        // either empty (we don't enter this block) or exactly the three
        // levels by construction.
        $fl = $by_level[$lvl];
        foreach (['low', 'high'] as $bound) {
            if ($fl[$bound] !== null) {
                $unit = $fl[$bound . '_data_type'] === 'flow' ? ' CFS' : ' ft';
                $parts[] = number_format((float)$fl[$bound], $fl[$bound . '_data_type'] === 'flow' ? 0 : 1) . $unit;
            }
        }
        $cells[] = '<td style="text-align:center">' . implode(' – ', $parts) . '</td>';
    }
    echo '<tr>' . implode('', $cells) . "</tr>\n";
    echo '</table>';
}

// Guidebooks
if ($guidebooks || $reach['aw_id']) {
    echo '<h3 style="margin-top:1rem">Guidebooks</h3>';
    echo '<table class="desc-table">';
    if ($reach['aw_id']) {
        $aw_url = "https://www.americanwhitewater.org/content/River/view/river-detail/"
            . intval($reach['aw_id']) . "/";
        echo '<tr><td><a href="' . htmlspecialchars($aw_url) . '" target="_blank" rel="noopener">American Whitewater</a></td><td></td></tr>' . "\n";
    }
    foreach ($guidebooks as $gb) {
        $title = htmlspecialchars($gb['title']);
        if ($gb['subtitle']) $title .= ' — ' . htmlspecialchars($gb['subtitle']);
        if ($gb['edition']) $title .= ' (' . htmlspecialchars($gb['edition']) . ')';
        $url = $gb['entry_url'] ?: $gb['book_url'];
        if ($url) {
            $title = '<a href="' . htmlspecialchars($url) . '" target="_blank" rel="noopener">' . $title . '</a>';
        }
        $detail = [];
        if ($gb['page']) $detail[] = 'p. ' . htmlspecialchars($gb['page']);
        if ($gb['run']) $detail[] = 'run ' . htmlspecialchars($gb['run']);
        echo "<tr><td>$title</td><td>" . implode(', ', $detail) . "</td></tr>\n";
    }
    echo '</table>';
}

// Linked gauge
if ($gauge) {
    echo '<h3 style="margin-top:1rem">Linked Gauge</h3>';
    echo '<table class="desc-table">';
    $gname = htmlspecialchars($gauge['display_name'] ?: $gauge['name']);
    $gloc = htmlspecialchars($gauge['location'] ?? '');
    echo "<tr><td>Gauge</td><td><a href=\"/gauge.php?id={$gauge['id']}\">$gname</a></td></tr>\n";
    if ($gloc) echo "<tr><td>Location</td><td>$gloc</td></tr>\n";
    echo '</table>';
}

// Map
$map_points = [];
if ($reach['latitude_start'] !== null && $reach['longitude_start'] !== null) {
    $map_points['Put-in'] = number_format((float)$reach['latitude_start'], 6, '.', '')
        . ',' . number_format((float)$reach['longitude_start'], 6, '.', '');
}
if ($reach['latitude_end'] !== null && $reach['longitude_end'] !== null) {
    $map_points['Take-out'] = number_format((float)$reach['latitude_end'], 6, '.', '')
        . ',' . number_format((float)$reach['longitude_end'], 6, '.', '');
}
if ($gauge && $gauge['latitude'] !== null && $gauge['longitude'] !== null) {
    $map_points['Gauge'] = number_format((float)$gauge['latitude'], 6, '.', '')
        . ',' . number_format((float)$gauge['longitude'], 6, '.', '');
}

if ($map_points || $reach['geom']) {
    $track = null;
    if (!empty($reach['geom'])) {
        $track = [];
        foreach (explode(',', $reach['geom']) as $pair) {
            $parts = preg_split('/\s+/', trim($pair));
            if (count($parts) === 2) {
                $track[] = [(float)$parts[1], (float)$parts[0]];
            }
        }
    }

    $leaflet_css = file_get_contents($_SERVER['DOCUMENT_ROOT'] . '/static/leaflet.css');
    echo '<style>' . $leaflet_css . '</style>';
    $pts_json = htmlspecialchars(json_encode($map_points));
    echo '<div id="reach-map" style="height:400px;margin-top:1rem;border:1px solid #ccc" data-points="' . $pts_json . '"';
    if ($track) {
        $track_json = htmlspecialchars(json_encode($track));
        echo ' data-track="' . $track_json . '"';
    }
    echo '></div>';
    $has_map = true;
    $map_scripts = '<script src="/static/leaflet.js" defer></script><script src="/static/reach-map.js" defer></script>';
}

// Footer links
echo '<p style="margin-top:1rem">';
echo '<a href="/description.php?id=' . $id . '">Description</a>';
echo ' | <a href="/data.php?id=' . $id . '">Data inspector</a>';
echo ' | <a href="/index.html">Back to main page</a></p>';

if ($has_map) echo $map_scripts;
include_footer();
