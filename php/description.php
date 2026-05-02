<?php
declare(strict_types=1);
/**
 * Reach description page — readings, plots, map link, metadata.
 *
 * Usage: /description.php?id=<reach_id>
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/html.php';
require_once __DIR__ . '/includes/svg_plot.php';
require_once __DIR__ . '/includes/gauge_plots.php';
require_once __DIR__ . '/includes/gauge_map.php';
require_once __DIR__ . '/includes/validate.php';

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$start_date = validate_date(filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS));
$end_date = validate_date(filter_input(INPUT_GET, 'end', FILTER_SANITIZE_SPECIAL_CHARS));
$hidden = filter_input(INPUT_GET, 'hidden', FILTER_VALIDATE_INT);
$hidden = ($hidden === 1) ? 1 : 0;
if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();
$has_map = false;

$reach = get_reach_or_404($id);

$name = $reach['display_name'] ?: $reach['name'];

// --- Navigation by sort_name, independent of gauge status ---
$prev_stmt = $db->prepare('SELECT id FROM reach WHERE (sort_name < ? OR (sort_name = ? AND id < ?)) AND no_show = ? ORDER BY sort_name DESC, id DESC LIMIT 1');
$prev_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
$prev = $prev_stmt->fetch();

$next_stmt = $db->prepare('SELECT id FROM reach WHERE (sort_name > ? OR (sort_name = ? AND id > ?)) AND no_show = ? ORDER BY sort_name ASC, id ASC LIMIT 1');
$next_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
$next = $next_stmt->fetch();

$total_stmt = $db->prepare('SELECT COUNT(*) FROM reach WHERE no_show = ?');
$total_stmt->execute([$hidden]);
$total = $total_stmt->fetchColumn();
$pos_stmt = $db->prepare('SELECT COUNT(*) FROM reach WHERE (sort_name < ? OR (sort_name = ? AND id <= ?)) AND no_show = ?');
$pos_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id, $hidden]);
$position = $pos_stmt->fetchColumn();

// Load gauge info
$gauge = null;
if ($reach['gauge_id']) {
    $stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
    $stmt->execute([$reach['gauge_id']]);
    $gauge = $stmt->fetch();
}

// Load states
$states_stmt = $db->prepare(
    'SELECT s.name FROM state s JOIN reach_state rs ON s.id = rs.state_id WHERE rs.reach_id = ?'
);
$states_stmt->execute([$id]);
$states = array_column($states_stmt->fetchAll(), 'name');

// Load classes
$classes_stmt = $db->prepare('SELECT name FROM reach_class WHERE reach_id = ?');
$classes_stmt->execute([$id]);
$classes = array_column($classes_stmt->fetchAll(), 'name');

// Derive low/okay/high bands from the reach's primary class range.
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

header('Cache-Control: max-age=300');
$preconnects = '<link rel="preconnect" href="https://a.tile.opentopomap.org">'
    . '<link rel="preconnect" href="https://b.tile.opentopomap.org">'
    . '<link rel="preconnect" href="https://c.tile.opentopomap.org">';
include_header(
    "$name - Description",
    '',
    "Real-time river data for $name — flow, gage height, and conditions.",
    $preconnects,
    ['type' => 'reach', 'id' => $id]
);

// Navigation bar (prev/next by sort order, independent of gauge status)
echo '<nav aria-label="Reach navigation" style="display:flex;align-items:center;gap:1rem;margin-bottom:.5rem;flex-wrap:wrap">';
$hq = $hidden ? '&amp;hidden=1' : '';
if ($prev) {
    echo '<a href="/description.php?id=' . $prev['id'] . $hq . '">&laquo; Prev</a>';
} else {
    echo '<span style="color:#999">&laquo; Prev</span>';
}
echo "<span>Reach $position of $total</span>";
if ($next) {
    echo '<a href="/description.php?id=' . $next['id'] . $hq . '">Next &raquo;</a>';
} else {
    echo '<span style="color:#999">Next &raquo;</span>';
}
echo '</nav>';

echo '<h2>' . htmlspecialchars($name) . '</h2>';

// --- Current readings ---
$readings = [];
if ($gauge) {
    $stmt = $db->prepare(
        'SELECT data_type, value, observed_at, delta_per_hour
         FROM latest_gauge_observation WHERE gauge_id = ?'
    );
    $stmt->execute([$gauge['id']]);
    $readings = $stmt->fetchAll();
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
            // Format value by data type: flow=integer, gauge/temperature=1 decimal
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

// --- Date range selector and inline SVG plots ---
if ($gauge) {
    [$latest_ts, $since, $until, $is_default_view] =
        gp_resolve_window($db, (int)$gauge['id'], $start_date, $end_date);
    gp_render_date_form(
        $id,
        $start_date,
        $end_date,
        $latest_ts,
        [['label' => 'Data inspector', 'url' => "/data.php?id=$id"]]
    );
    gp_render_plots($db, (int)$gauge['id'], $name, $since, $until, $latest_ts, $is_default_view, $class_range ?: null);
}

// --- Description fields ---
echo '<table class="desc-table">';

$fields = [
    'Class' => implode(', ', $classes),
    'State' => implode(', ', $states),
    'Watershed' => $reach['basin'],
    'Region' => $reach['region'],
    'Gauge' => $gauge ? $gauge['location'] : null,
    'Season' => $reach['season'],
    'Length' => $reach['length'] ? $reach['length'] . ' mi' : null,
    'Gradient' => $reach['gradient'] ? $reach['gradient'] . ' ft/mi' : null,
    'Elevation Loss' => $reach['elevation_lost'] ? $reach['elevation_lost'] . ' ft' : null,
    'Scenery' => $reach['scenery'],
    'Features' => $reach['features'],
    'Remoteness' => $reach['remoteness'],
    'Nature' => $reach['nature'],
    'Watershed type' => $reach['watershed_type'],
    'Optimal Flow' => $reach['optimal_flow'] ? number_format((float)$reach['optimal_flow'], 0) . ' CFS' : null,
];

// Insert flow level rows
foreach ($flow_levels as $fl) {
    $parts = [];
    if ($fl['low'] !== null) {
        $unit = $fl['low_data_type'] === 'flow' ? ' CFS' : ' ft';
        $parts[] = number_format((float)$fl['low'], $fl['low_data_type'] === 'flow' ? 0 : 1) . $unit;
    }
    if ($fl['high'] !== null) {
        $unit = $fl['high_data_type'] === 'flow' ? ' CFS' : ' ft';
        $parts[] = number_format((float)$fl['high'], $fl['high_data_type'] === 'flow' ? 0 : 1) . $unit;
    }
    if ($parts) {
        $label = ucfirst($fl['level']) . ' Flow';
        $fields[$label] = implode(' – ', $parts);
    }
}

// Build coordinate fields as Google Maps links (Gauge, Put-in, Take-out contiguous)
$map_points = []; // label => "lat,lon" for combined map link
$coord_fields = [];

if ($gauge && $gauge['latitude'] !== null && $gauge['longitude'] !== null) {
    $glat = number_format((float)$gauge['latitude'], 5, '.', '');
    $glon = number_format((float)$gauge['longitude'], 5, '.', '');
    $coord_fields['Gauge Location'] = [$glat, $glon];
    $map_points['Gauge'] = "$glat,$glon";
}
if ($reach['latitude_start'] !== null && $reach['longitude_start'] !== null) {
    $slat = number_format((float)$reach['latitude_start'], 5, '.', '');
    $slon = number_format((float)$reach['longitude_start'], 5, '.', '');
    $coord_fields['Put-in'] = [$slat, $slon];
    $map_points['Put-in'] = "$slat,$slon";
}
if ($reach['latitude_end'] !== null && $reach['longitude_end'] !== null) {
    $elat = number_format((float)$reach['latitude_end'], 5, '.', '');
    $elon = number_format((float)$reach['longitude_end'], 5, '.', '');
    $coord_fields['Take-out'] = [$elat, $elon];
    $map_points['Take-out'] = "$elat,$elon";
}

foreach ($coord_fields as $label => $coords) {
    $url = "https://www.google.com/maps?q={$coords[0]},{$coords[1]}";
    $fields[$label] = "<a href=\"" . htmlspecialchars($url) . "\" target=\"_blank\" rel=\"noopener\">{$coords[0]}, {$coords[1]}</a>";
}

$fields += [
    'Difficulties' => $reach['difficulties'],
    'Description' => $reach['description'],
    'Notes' => $reach['notes'],
];

// Determine flow level for river track color
$track_color = '#2196F3'; // blue = unknown
if ($flow_levels && $readings) {
    // Find current value matching a level's data_type
    $reading_by_type = [];
    foreach ($readings as $r) {
        $reading_by_type[$r['data_type']] = (float)$r['value'];
    }
    foreach ($flow_levels as $fl) {
        $dtype = $fl['low_data_type'];
        if (isset($reading_by_type[$dtype])) {
            $val = $reading_by_type[$dtype];
            $lo = $fl['low'] !== null ? (float)$fl['low'] : null;
            $hi = $fl['high'] !== null ? (float)$fl['high'] : null;
            $in_range = ($lo === null || $val >= $lo) && ($hi === null || $val <= $hi);
            if ($in_range) {
                $level_colors = ['low' => '#e8a735', 'okay' => '#4caf50', 'high' => '#e53935'];
                $track_color = $level_colors[$fl['level']];
                break;
            }
        }
    }
}

// Inline map with labeled markers and river track (Leaflet + OpenStreetMap)
$geom = $reach['geom'] ?? null;
if (count($map_points) >= 1 || $geom) {
    echo '</table>';
    $has_map = gm_render_map($map_points, $geom, $track_color);
    echo '<table class="desc-table">';
}

// HTML-safe fields list for raw output
$html_fields = ['Gauge Location', 'Put-in', 'Take-out'];
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

// --- Data sources ---
if ($gauge) {
    $src_stmt = $db->prepare(
        'SELECT s.name, s.agency, f.url AS fetch_url, c.expression AS calc_expr
         FROM source s
         JOIN gauge_source gs ON gs.source_id = s.id
         LEFT JOIN fetch_url f ON s.fetch_url_id = f.id
         LEFT JOIN calc_expression c ON s.calc_expression_id = c.id
         WHERE gs.gauge_id = ?'
    );
    $src_stmt->execute([$gauge['id']]);
    $sources = $src_stmt->fetchAll();

    if ($sources) {
        echo '<h3 style="margin-top:1rem">Data Sources</h3>';
        echo '<table class="desc-table">';

        // Build station page URLs for known gauge IDs
        $station_urls = [];
        if (!empty($gauge['usgs_id'])) {
            $station_urls['USGS'] = [
                'label' => 'USGS - ' . $gauge['usgs_id'],
                'url' => "https://waterdata.usgs.gov/monitoring-location/USGS-"
                    . urlencode($gauge['usgs_id'])
                    . "/#dataTypeId=continuous-00065-0&period=P7D&showFieldMeasurements=true",
            ];
        }
        if (!empty($gauge['nwsli_id'])) {
            $station_urls['NWRFC'] = [
                'label' => 'NWRFC - ' . $gauge['nwsli_id'],
                'url' => "https://www.nwrfc.noaa.gov/river/station/flowplot/flowplot.cgi?lid="
                    . urlencode($gauge['nwsli_id']),
            ];
        }

        $shown_agencies = [];
        foreach ($sources as $src) {
            // Match source to a station page link by agency
            $matched = null;
            $agency = $src['agency'] ?? '';
            foreach ($station_urls as $key => $info) {
                if (in_array($key, $shown_agencies)) continue;
                if (stripos($agency, $key) !== false) {
                    $matched = $key;
                    break;
                }
                // NWS sources match NWRFC gauge pages
                if ($key === 'NWRFC' && stripos($agency, 'NWS') !== false) {
                    $matched = $key;
                    break;
                }
            }

            if ($matched) {
                $shown_agencies[] = $matched;
                $info = $station_urls[$matched];
                $label = '<a href="' . htmlspecialchars($info['url']) . '" target="_blank" rel="noopener">'
                    . htmlspecialchars($info['label']) . '</a>';
            } else {
                $src_name = htmlspecialchars($src['name']);
                $agency = $src['agency'] ? htmlspecialchars($src['agency']) : '';
                $label = $agency ? "$agency — $src_name" : $src_name;
            }

            if ($src['fetch_url']) {
                $url = htmlspecialchars($src['fetch_url']);
                echo "<tr><td>$label</td><td><a href=\"$url\" target=\"_blank\" rel=\"noopener\">$url</a></td></tr>\n";
            } elseif ($src['calc_expr']) {
                // Link gauge references like "nP::S_Santiam_Cascadia_merge::flow"
                // The middle token is a gauge name; find a reach that uses it.
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
                        $r = $stmt->fetch();
                        $full = htmlspecialchars($m[0]);
                        if ($r) {
                            $display = htmlspecialchars($r['display_name'] ?: $gauge_name);
                            return "<a href=\"/description.php?id={$r['id']}\" title=\"$full\">$display</a>::{$m[3]}";
                        }
                        return $full;
                    },
                    $src['calc_expr']
                );
                echo "<tr><td>$label</td><td>Calculated: $expr_html</td></tr>\n";
            } else {
                echo "<tr><td>$label</td><td>—</td></tr>\n";
            }
        }
        // Show any station page links that didn't match a source row
        foreach ($station_urls as $key => $info) {
            if (!in_array($key, $shown_agencies)) {
                $label = '<a href="' . htmlspecialchars($info['url']) . '" target="_blank" rel="noopener">'
                    . htmlspecialchars($info['label']) . '</a>';
                echo "<tr><td>$label</td><td></td></tr>\n";
            }
        }
        echo '</table>';
    }
}

// --- Guidebooks ---
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

$btn_style = 'display:inline-flex;align-items:center;min-height:44px;padding:8px 12px';
echo '<nav style="margin-top:1rem;display:flex;flex-wrap:wrap;gap:.5rem">';
echo '<a href="/index.html" style="' . $btn_style . '">Back to main page</a>';
echo '<a href="/reach.php?id=' . $id . '" style="' . $btn_style . '">Reach details</a>';
if (editor_feature_enabled()) {
    $editor = current_editor();
    if (is_maintainer($editor)) {
        echo '<a href="/edit.php?id=' . $id . '" style="' . $btn_style . '">Edit</a>';
    } elseif ($editor !== null) {
        echo '<a href="/propose.php?type=reach&amp;id=' . $id . '" style="' . $btn_style . '">Suggest an edit</a>';
    } else {
        $next = rawurlencode("/propose.php?type=reach&id=$id");
        echo '<a href="/login.php?next=' . $next . '" style="' . $btn_style . '">Sign in to suggest an edit</a>';
    }
}
echo '</nav>';

if ($has_map) {
    echo '<script src="/static/leaflet.js" defer></script>';
    echo '<script src="/static/feature-map.js" defer></script>';
}

include_footer();
