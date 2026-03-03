<?php
/**
 * Gauge browser — view gauge details with associated sources and reaches.
 *
 * Usage: /gauge.php?id=<gauge_id> or /gauge.php?q=<search>
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

$db = get_db();

$id = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$q  = filter_input(INPUT_GET, 'q', FILTER_DEFAULT);

// --- Search mode ---
if ($q !== null && $q !== '') {
    $q = trim($q);
    $stmt = $db->prepare('SELECT id, name, location FROM gauge WHERE name LIKE ? ORDER BY id');
    $stmt->execute(["%$q%"]);
    $results = $stmt->fetchAll();

    if (count($results) === 1) {
        header('Location: /gauge.php?id=' . $results[0]['id']);
        exit;
    }

    header('Cache-Control: no-cache');
    include_header('Gauge Search');
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
        include_header('Gauges');
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
    'SELECT s.id, s.name, s.agency
     FROM source s
     JOIN gauge_source gs ON s.id = gs.source_id
     WHERE gs.gauge_id = ?
     ORDER BY s.name'
);
$sources_stmt->execute([$id]);
$sources = $sources_stmt->fetchAll();

// --- Associated reaches ---
$reaches_stmt = $db->prepare(
    'SELECT id, COALESCE(display_name, name) AS name FROM reach WHERE gauge_id = ? ORDER BY sort_name'
);
$reaches_stmt->execute([$id]);
$reaches = $reaches_stmt->fetchAll();

// --- Render ---
header('Cache-Control: no-cache');
include_header(htmlspecialchars($gauge['name']) . ' - Gauge');

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

// Gauge details
echo '<h2>' . htmlspecialchars($gauge['name']) . '</h2>';
echo '<table class="desc-table">';

$fields = [
    'ID' => $gauge['id'],
    'Name' => $gauge['name'],
    'Location' => $gauge['location'],
    'Station ID' => $gauge['station_id'],
    'USGS ID' => $gauge['usgs_id'],
    'CBTT ID' => $gauge['cbtt_id'],
    'GEOS ID' => $gauge['geos_id'],
    'NWS ID' => $gauge['nws_id'],
    'NWSLI ID' => $gauge['nwsli_id'],
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

if ($gauge['bank_full'] !== null) {
    $fields['Bank Full'] = number_format((float)$gauge['bank_full'], 2);
}
if ($gauge['flood_stage'] !== null) {
    $fields['Flood Stage'] = number_format((float)$gauge['flood_stage'], 2);
}

foreach ($fields as $label => $value) {
    if ($value === null || trim((string)$value) === '') continue;
    if ($label === 'Coordinates') {
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
    echo '<table class="desc-table">';
    echo '<tr><th>ID</th><th>Name</th><th>Agency</th></tr>';
    foreach ($sources as $s) {
        $sname = htmlspecialchars($s['name']);
        $sagency = htmlspecialchars($s['agency'] ?? '');
        echo "<tr><td>{$s['id']}</td><td><a href=\"/source.php?id={$s['id']}\">$sname</a></td><td>$sagency</td></tr>\n";
    }
    echo '</table>';
} else {
    echo '<p style="margin-top:1rem;color:#666">No associated sources.</p>';
}

// Associated reaches
if ($reaches) {
    echo '<h3 style="margin-top:1rem">Associated Reaches</h3>';
    echo '<table class="desc-table">';
    echo '<tr><th>ID</th><th>Name</th></tr>';
    foreach ($reaches as $r) {
        $rname = htmlspecialchars($r['name']);
        echo "<tr><td>{$r['id']}</td><td><a href=\"/description.php?id={$r['id']}\">$rname</a></td></tr>\n";
    }
    echo '</table>';
} else {
    echo '<p style="margin-top:1rem;color:#666">No associated reaches.</p>';
}

echo '<p style="margin-top:1rem"><a href="/index.html">Back to main page</a></p>';

include_footer();
