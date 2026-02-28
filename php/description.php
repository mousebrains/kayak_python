<?php
/**
 * Section description page — readings, plots, map link, metadata.
 *
 * Usage: /description.php?id=<section_id>
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/svg_plot.php';

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
if (!$id) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();

$section = $db->prepare('SELECT * FROM section WHERE id = ?');
$section->execute([$id]);
$section = $section->fetch();
if (!$section) { http_response_code(404); exit('Section not found'); }

$name = $section['display_name'] ?: $section['name'];

// Load gauge info
$gauge = null;
if ($section['gauge_id']) {
    $stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
    $stmt->execute([$section['gauge_id']]);
    $gauge = $stmt->fetch();
}

// Load states
$states_stmt = $db->prepare(
    'SELECT s.name FROM state s JOIN section_state ss ON s.id = ss.state_id WHERE ss.section_id = ?'
);
$states_stmt->execute([$id]);
$states = array_column($states_stmt->fetchAll(), 'name');

// Load classes
$classes_stmt = $db->prepare('SELECT name FROM section_class WHERE section_id = ?');
$classes_stmt->execute([$id]);
$classes = array_column($classes_stmt->fetchAll(), 'name');

header('Cache-Control: max-age=300');
include_header("$name - Description");

echo '<h2>' . htmlspecialchars($name) . '</h2>';

// --- Google Maps link ---
$lat = $gauge['latitude'] ?? $section['latitude'] ?? null;
$lon = $gauge['longitude'] ?? $section['longitude'] ?? null;
if ($lat !== null && $lon !== null) {
    $lat_f = number_format((float)$lat, 6, '.', '');
    $lon_f = number_format((float)$lon, 6, '.', '');
    $maps_url = "https://www.google.com/maps?q={$lat_f},{$lon_f}";
    echo '<p style="margin:.5rem 0"><a href="' . htmlspecialchars($maps_url) . '" target="_blank" rel="noopener">View on Google Maps</a></p>';
}

// --- Current readings ---
$source_id = null;
if ($gauge) {
    $stmt = $db->prepare('SELECT source_id FROM gauge_source WHERE gauge_id = ? LIMIT 1');
    $stmt->execute([$gauge['id']]);
    $gs = $stmt->fetch();
    if ($gs) {
        $source_id = $gs['source_id'];
    }
}

if ($source_id) {
    $stmt = $db->prepare(
        'SELECT data_type, value, observed_at, delta_per_hour
         FROM latest_observation WHERE source_id = ?'
    );
    $stmt->execute([$source_id]);
    $readings = $stmt->fetchAll();

    if ($readings) {
        echo '<table class="readings-table">';
        echo '<tr><th>Type</th><th>Value</th><th>Time</th><th>Change/hr</th><th>Status</th></tr>';
        foreach ($readings as $r) {
            $dtype = htmlspecialchars($r['data_type']);
            // Format value by data type: flow=integer, gauge/temperature=1 decimal
            $raw = (float)$r['value'];
            if ($r['data_type'] === 'flow') {
                $val = number_format($raw, 0);
            } else {
                $val = number_format($raw, 1);
            }
            $time_iso = $r['observed_at'] ? date('Y-m-d\TH:i:s\Z', strtotime($r['observed_at'])) : '';
            $time_display = $r['observed_at'] ? date('m/d H:i', strtotime($r['observed_at'])) : 'N/A';
            $time_html = $time_iso ? "<time datetime=\"$time_iso\">$time_display</time>" : 'N/A';
            $delta = $r['delta_per_hour'] !== null ? number_format((float)$r['delta_per_hour'], 2) : 'N/A';
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
            echo "<tr><td>$dtype</td><td>$val</td><td>$time_html</td><td>$delta</td><td>$status</td></tr>\n";
        }
        echo '</table>';
    }
}

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
        foreach ($sources as $src) {
            $src_name = htmlspecialchars($src['name']);
            $agency = $src['agency'] ? htmlspecialchars($src['agency']) : '';
            $label = $agency ? "$agency — $src_name" : $src_name;
            if ($src['fetch_url']) {
                $url = htmlspecialchars($src['fetch_url']);
                echo "<tr><td>$label</td><td><a href=\"$url\" target=\"_blank\" rel=\"noopener\">$url</a></td></tr>\n";
            } elseif ($src['calc_expr']) {
                $expr = htmlspecialchars($src['calc_expr']);
                echo "<tr><td>$label</td><td>Calculated: $expr</td></tr>\n";
            } else {
                echo "<tr><td>$label</td><td>—</td></tr>\n";
            }
        }
        echo '</table>';
    }
}

// --- Inline SVG plots (only for data types with observations) ---
if ($source_id) {
    $plot_types = [
        'flow'        => 'Flow (CFS)',
        'gauge'       => 'Gage Height (Ft)',
        'temperature' => 'Temperature (F)',
    ];
    $since = date('Y-m-d H:i:s', time() - 60 * 86400);

    foreach ($plot_types as $dtype => $y_label) {
        $stmt = $db->prepare(
            'SELECT observed_at, value FROM observation
             WHERE source_id = ? AND data_type = ? AND observed_at >= ?
             ORDER BY observed_at'
        );
        $stmt->execute([$source_id, $dtype, $since]);
        $rows = $stmt->fetchAll();

        if (count($rows) < 2) continue;

        $times = []; $values = [];
        foreach ($rows as $r) {
            $times[]  = strtotime($r['observed_at']);
            $values[] = (float)$r['value'];
        }

        $title = htmlspecialchars($name) . " — $y_label";
        $svg = generate_svg_plot($times, $values, $title, $y_label);
        echo '<div class="plot-container">' . $svg . '</div>';
    }
}

// --- Description fields ---
echo '<table class="desc-table">';

$fields = [
    'Class' => implode(', ', $classes),
    'State' => implode(', ', $states),
    'Drainage' => $section['basin'],
    'Region' => $section['region'],
    'Gauge' => $gauge ? $gauge['location'] : null,
    'Season' => $section['season'],
    'Length' => $section['length'] ? $section['length'] . ' mi' : null,
    'Gradient' => $section['gradient'] ? $section['gradient'] . ' ft/mi' : null,
    'Elevation Loss' => $section['elevation_lost'] ? $section['elevation_lost'] . ' ft' : null,
    'Scenery' => $section['scenery'],
    'Features' => $section['features'],
    'Remoteness' => $section['remoteness'],
    'Nature' => $section['nature'],
    'Watershed' => $section['watershed_type'],
    'Optimal Flow' => $section['optimal_flow'],
    'Difficulties' => $section['difficulties'],
    'Description' => $section['description'],
    'Notes' => $section['notes'],
];

foreach ($fields as $label => $value) {
    if ($value === null || trim((string)$value) === '') continue;
    $esc = htmlspecialchars((string)$value);
    echo "<tr><td>$label</td><td>$esc</td></tr>\n";
}

echo '</table>';
echo '<p style="margin-top:1rem"><a href="/index.html">Back to main page</a>';
echo ' | <a href="/edit.php?id=' . $id . '">Edit</a></p>';

include_footer();
