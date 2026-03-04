<?php
/**
 * Reach browser — view reach details with navigation.
 *
 * Usage: /reach.php?id=<reach_id> or /reach.php?q=<search>
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
    $stmt = $db->prepare(
        'SELECT id, COALESCE(NULLIF(display_name, \'\'), name) AS name, river
         FROM reach
         WHERE display_name LIKE ? OR name LIKE ? OR river LIKE ?
         ORDER BY sort_name'
    );
    $pat = "%$q%";
    $stmt->execute([$pat, $pat, $pat]);
    $results = $stmt->fetchAll();

    if (count($results) === 1) {
        header('Location: /reach.php?id=' . $results[0]['id']);
        exit;
    }

    header('Cache-Control: no-cache');
    include_header('Reach Search');
    echo '<h2>Reach Search</h2>';

    if (!$results) {
        echo '<p>No reaches matching &ldquo;' . htmlspecialchars($q) . '&rdquo;.</p>';
    } else {
        echo '<p>' . count($results) . ' reaches matching &ldquo;' . htmlspecialchars($q) . '&rdquo;:</p>';
        echo '<table class="desc-table">';
        echo '<tr><th>ID</th><th>Name</th><th>River</th></tr>';
        foreach ($results as $r) {
            $rname = htmlspecialchars($r['name']);
            $river = htmlspecialchars($r['river'] ?? '');
            echo "<tr><td>{$r['id']}</td><td><a href=\"/reach.php?id={$r['id']}\">$rname</a></td><td>$river</td></tr>\n";
        }
        echo '</table>';
    }

    echo '<p style="margin-top:1rem"><a href="/reach.php">Browse all reaches</a></p>';
    include_footer();
    exit;
}

// --- Default: show first reach ---
if (!$id) {
    $row = $db->query('SELECT id FROM reach ORDER BY sort_name, id ASC LIMIT 1')->fetch();
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
$prev_stmt = $db->prepare('SELECT id FROM reach WHERE sort_name < ? OR (sort_name = ? AND id < ?) ORDER BY sort_name DESC, id DESC LIMIT 1');
$prev_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id]);
$prev = $prev_stmt->fetch();

$next_stmt = $db->prepare('SELECT id FROM reach WHERE sort_name > ? OR (sort_name = ? AND id > ?) ORDER BY sort_name ASC, id ASC LIMIT 1');
$next_stmt->execute([$reach['sort_name'], $reach['sort_name'], $id]);
$next = $next_stmt->fetch();

$total = $db->query('SELECT COUNT(*) FROM reach')->fetchColumn();
$pos = $db->prepare('SELECT COUNT(*) FROM reach WHERE sort_name < ? OR (sort_name = ? AND id <= ?)');
$pos->execute([$reach['sort_name'], $reach['sort_name'], $id]);
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

$levels_stmt = $db->prepare(
    'SELECT level, low, low_data_type, high, high_data_type FROM reach_level WHERE reach_id = ? ORDER BY level'
);
$levels_stmt->execute([$id]);
$flow_levels = $levels_stmt->fetchAll();

$gb_stmt = $db->prepare(
    'SELECT g.title, g.subtitle, g.edition, g.author, g.url AS book_url,
            rg.page, rg.run, rg.url AS entry_url
     FROM reach_guidebook rg
     JOIN guidebook g ON g.id = rg.guidebook_id
     WHERE rg.reach_id = ?
     ORDER BY g.title, g.edition'
);
$gb_stmt->execute([$id]);
$guidebooks = $gb_stmt->fetchAll();

// --- Render ---
header('Cache-Control: no-cache');
include_header(htmlspecialchars($name) . ' - Reach');

// Navigation bar
echo '<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;flex-wrap:wrap">';
if ($prev) {
    echo '<a href="/reach.php?id=' . $prev['id'] . '">&laquo; Prev</a>';
} else {
    echo '<span style="color:#999">&laquo; Prev</span>';
}
echo "<span>Reach $position of $total</span>";
if ($next) {
    echo '<a href="/reach.php?id=' . $next['id'] . '">Next &raquo;</a>';
} else {
    echo '<span style="color:#999">Next &raquo;</span>';
}
echo '<form method="get" action="/reach.php" style="display:flex;gap:.25rem;margin-left:auto">';
echo '<input type="text" name="q" placeholder="Search reaches…" style="width:14rem">';
echo '<button type="submit">Go</button>';
echo '</form>';
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
    'Basin' => $reach['basin'],
    'Basin Area' => $reach['basin_area'] ? number_format((float)$reach['basin_area'], 1) . ' sq mi' : null,
    'Region' => $reach['region'],
    'Season' => $reach['season'],
    'Nature' => $reach['nature'],
    'Watershed' => $reach['watershed_type'],
    'Scenery' => $reach['scenery'],
    'Remoteness' => $reach['remoteness'],
    'Features' => $reach['features'],
    'Length' => $reach['length'] ? number_format((float)$reach['length'], 1) . ' mi' : null,
    'Gradient' => $reach['gradient'] ? number_format((float)$reach['gradient'], 0) . ' ft/mi' : null,
    'Max Gradient' => $reach['max_gradient'] ? number_format((float)$reach['max_gradient'], 0) . ' ft/mi' : null,
    'Elevation' => $reach['elevation'] ? number_format((float)$reach['elevation'], 0) . ' ft' : null,
    'Elevation Lost' => $reach['elevation_lost'] ? number_format((float)$reach['elevation_lost'], 0) . ' ft' : null,
    'Optimal Flow' => $reach['optimal_flow'] ? number_format((float)$reach['optimal_flow'], 0) . ' CFS' : null,
    'Map Name' => $reach['map_name'],
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

// AW link
if ($reach['aw_id']) {
    $aw_url = "https://www.americanwhitewater.org/content/River/view/river-detail/"
        . intval($reach['aw_id']) . "/";
    $fields['AW ID'] = '<a href="' . htmlspecialchars($aw_url) . '" target="_blank" rel="noopener">'
        . intval($reach['aw_id']) . '</a>';
}

$fields += [
    'Description' => $reach['description'],
    'Difficulties' => $reach['difficulties'],
    'Notes' => $reach['notes'],
];

$html_fields = ['Put-in', 'Take-out', 'AW ID'];
foreach ($fields as $label => $value) {
    if ($value === null || trim((string)$value) === '') continue;
    if (in_array($label, $html_fields)) {
        echo "<tr><td>$label</td><td>$value</td></tr>\n";
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

// Flow levels
if ($flow_levels) {
    echo '<h3 style="margin-top:1rem">Flow Levels</h3>';
    echo '<table class="desc-table">';
    echo '<tr><th>Level</th><th>Low</th><th>High</th></tr>';
    foreach ($flow_levels as $fl) {
        $level = htmlspecialchars(ucfirst($fl['level']));
        $lo = '';
        if ($fl['low'] !== null) {
            $unit = $fl['low_data_type'] === 'flow' ? ' CFS' : ' ft';
            $lo = number_format((float)$fl['low'], $fl['low_data_type'] === 'flow' ? 0 : 1) . $unit;
        }
        $hi = '';
        if ($fl['high'] !== null) {
            $unit = $fl['high_data_type'] === 'flow' ? ' CFS' : ' ft';
            $hi = number_format((float)$fl['high'], $fl['high_data_type'] === 'flow' ? 0 : 1) . $unit;
        }
        echo "<tr><td>$level</td><td>$lo</td><td>$hi</td></tr>\n";
    }
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
    $gname = htmlspecialchars($gauge['name']);
    $gloc = htmlspecialchars($gauge['location'] ?? '');
    echo "<tr><td>Gauge</td><td><a href=\"/gauge.php?id={$gauge['id']}\">$gname</a></td></tr>\n";
    if ($gloc) echo "<tr><td>Location</td><td>$gloc</td></tr>\n";
    echo '</table>';
}

// Footer links
echo '<p style="margin-top:1rem">';
echo '<a href="/description.php?id=' . $id . '">Description</a>';
echo ' | <a href="/data.php?id=' . $id . '">Data inspector</a>';
echo ' | <a href="/index.html">Back to main page</a></p>';

include_footer();
