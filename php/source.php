<?php
declare(strict_types=1);
/**
 * Source browser — view source details with associated gauges.
 *
 * Usage: /source.php?id=<source_id> or /source.php?q=<search>
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
        'SELECT id, name, agency FROM source
         WHERE name LIKE ? OR agency LIKE ?
         ORDER BY id
         LIMIT 200'
    );
    $pat = "%$q%";
    $stmt->execute([$pat, $pat]);
    $results = $stmt->fetchAll();

    if (count($results) === 1) {
        header('Location: /source.php?id=' . $results[0]['id']);
        exit;
    }

    header('Cache-Control: no-cache');
    include_header('Source Search', '', '', '', ['picker_kind' => 'gauge']);
    echo '<h2>Source Search</h2>';

    if (!$results) {
        echo '<p>No sources matching &ldquo;' . htmlspecialchars($q) . '&rdquo;.</p>';
    } else {
        echo '<p>' . count($results) . ' sources matching &ldquo;' . htmlspecialchars($q) . '&rdquo;:</p>';
        echo '<table class="desc-table">';
        echo '<tr><th>ID</th><th>Name</th><th>Agency</th></tr>';
        foreach ($results as $r) {
            $name = htmlspecialchars($r['name']);
            $agency = htmlspecialchars($r['agency'] ?? '');
            echo "<tr><td>{$r['id']}</td><td><a href=\"/source.php?id={$r['id']}\">$name</a></td><td>$agency</td></tr>\n";
        }
        echo '</table>';
    }

    echo '<p style="margin-top:1rem"><a href="/source.php">Browse all sources</a></p>';
    include_footer();
    exit;
}

// --- Default: show first source ---
if (!$id) {
    $row = $db->query('SELECT id FROM source ORDER BY id ASC LIMIT 1')->fetch();
    if (!$row) {
        header('Cache-Control: no-cache');
        include_header('Sources', '', '', '', ['picker_kind' => 'gauge']);
        echo '<p>No sources in database.</p>';
        include_footer();
        exit;
    }
    $id = $row['id'];
}

// --- Load current source ---
$stmt = $db->prepare(
    'SELECT s.*, f.url AS fetch_url, f.parser AS fetch_parser,
            c.expression AS calc_expr, c.data_type AS calc_data_type, c.note AS calc_note
     FROM source s
     LEFT JOIN fetch_url f ON s.fetch_url_id = f.id
     LEFT JOIN calc_expression c ON s.calc_expression_id = c.id
     WHERE s.id = ?'
);
$stmt->execute([$id]);
$source = $stmt->fetch();
if (!$source) { http_response_code(404); exit('Source not found'); }

// --- Navigation ---
$prev_stmt = $db->prepare('SELECT id FROM source WHERE id < ? ORDER BY id DESC LIMIT 1');
$prev_stmt->execute([$id]);
$prev = $prev_stmt->fetch();

$next_stmt = $db->prepare('SELECT id FROM source WHERE id > ? ORDER BY id ASC LIMIT 1');
$next_stmt->execute([$id]);
$next = $next_stmt->fetch();

$total = $db->query('SELECT COUNT(*) FROM source')->fetchColumn();
$pos = $db->prepare('SELECT COUNT(*) FROM source WHERE id <= ?');
$pos->execute([$id]);
$position = $pos->fetchColumn();

// --- Observation summary ---
$obs_stmt = $db->prepare(
    'SELECT data_type, COUNT(*) AS cnt, MAX(observed_at) AS latest
     FROM observation WHERE source_id = ? GROUP BY data_type ORDER BY data_type'
);
$obs_stmt->execute([$id]);
$obs_summary = $obs_stmt->fetchAll();

// --- Associated gauges ---
$gauges_stmt = $db->prepare(
    'SELECT g.id, g.name, g.location, g.usgs_id
     FROM gauge g
     JOIN gauge_source gs ON g.id = gs.gauge_id
     WHERE gs.source_id = ?
     ORDER BY g.name'
);
$gauges_stmt->execute([$id]);
$gauges = $gauges_stmt->fetchAll();

// --- Render ---
header('Cache-Control: no-cache');
include_header(
    $source['name'] . ' - Source',
    '', '', '',
    ['type' => 'source', 'id' => (int)$source['id']]
);

// Navigation bar
echo '<div style="display:flex;align-items:center;gap:1rem;margin-bottom:1rem;flex-wrap:wrap">';
if ($prev) {
    echo '<a href="/source.php?id=' . $prev['id'] . '">&laquo; Prev</a>';
} else {
    echo '<span style="color:#999">&laquo; Prev</span>';
}
echo "<span>Source $position of $total</span>";
if ($next) {
    echo '<a href="/source.php?id=' . $next['id'] . '">Next &raquo;</a>';
} else {
    echo '<span style="color:#999">Next &raquo;</span>';
}
echo '<form method="get" action="/source.php" style="display:flex;gap:.25rem;margin-left:auto">';
echo '<input type="text" name="q" placeholder="Search sources…" style="width:14rem">';
echo '<button type="submit">Go</button>';
echo '</form>';
echo '</div>';

// Source details
echo '<h2>' . htmlspecialchars($source['name']) . '</h2>';
echo '<table class="desc-table">';

// Build agency metadata URL for the source name
$agency_url = null;
$agency_lc = strtolower($source['agency'] ?? '');
$src_name = $source['name'] ?? '';
if ($agency_lc === 'usgs' && preg_match('/^\d+$/', $src_name)) {
    $agency_url = 'https://waterdata.usgs.gov/monitoring-location/' . urlencode($src_name);
} elseif ($agency_lc === 'nwrfc' && preg_match('/^[A-Z]{4}\d?$/', $src_name)) {
    $agency_url = 'https://www.nwrfc.noaa.gov/river/station/flowplot/flowplot.cgi?lid=' . urlencode($src_name);
} elseif ($agency_lc === 'usace') {
    $agency_url = 'https://www.nwp.usace.army.mil/Locations/' . urlencode($src_name);
} elseif ($agency_lc === 'usbr') {
    $agency_url = 'https://www.usbr.gov/pn/hydromet/arcread.html?station=' . urlencode($src_name);
} elseif ($agency_lc === 'idwr') {
    $agency_url = 'https://research.idwr.idaho.gov/apps/Realtime/Pages/StationPage.aspx?SiteID=' . urlencode($src_name);
}

$name_html = htmlspecialchars($src_name);
if ($agency_url) {
    $name_html = '<a href="' . htmlspecialchars($agency_url) . '" target="_blank" rel="noopener">' . $name_html . '</a>';
}

$fields = [
    'ID' => $source['id'],
    'Name' => $name_html,
    'Agency' => $source['agency'],
];

if ($source['fetch_url']) {
    $url_esc = htmlspecialchars($source['fetch_url']);
    $fields['Fetch URL'] = "<a href=\"$url_esc\" target=\"_blank\" rel=\"noopener\">$url_esc</a>";
    if ($source['fetch_parser']) {
        $fields['Parser'] = $source['fetch_parser'];
    }
} elseif ($source['calc_expr']) {
    $fields['Calc Expression'] = $source['calc_expr'];
    if ($source['calc_data_type']) {
        $fields['Calc Data Type'] = $source['calc_data_type'];
    }
    if ($source['calc_note']) {
        $fields['Calc Note'] = $source['calc_note'];
    }
}

foreach ($fields as $label => $value) {
    if ($value === null || trim((string)$value) === '') continue;
    // These fields already contain HTML links
    if ($label === 'Fetch URL' || $label === 'Name') {
        echo "<tr><td>$label</td><td>$value</td></tr>\n";
    } else {
        $esc = htmlspecialchars((string)$value);
        echo "<tr><td>$label</td><td>$esc</td></tr>\n";
    }
}

echo '</table>';

// Observation summary
if ($obs_summary) {
    echo '<h3 style="margin-top:1rem">Observations</h3>';
    echo '<table class="desc-table">';
    echo '<tr><th>Data Type</th><th>Count</th><th>Latest</th></tr>';
    foreach ($obs_summary as $o) {
        $dtype = htmlspecialchars($o['data_type']);
        $cnt = number_format((int)$o['cnt']);
        $latest = htmlspecialchars($o['latest'] ?? '');
        echo "<tr><td>$dtype</td><td>$cnt</td><td>$latest</td></tr>\n";
    }
    echo '</table>';
} else {
    echo '<p style="margin-top:1rem;color:#666">No observations.</p>';
}

// Associated gauges
if ($gauges) {
    echo '<h3 style="margin-top:1rem">Associated Gauges</h3>';
    echo '<table class="desc-table">';
    echo '<tr><th>ID</th><th>Name</th><th>Location</th><th>USGS ID</th></tr>';
    foreach ($gauges as $g) {
        $gname = htmlspecialchars($g['name']);
        $gloc = htmlspecialchars($g['location'] ?? '');
        $gusgs = htmlspecialchars($g['usgs_id'] ?? '');
        echo "<tr><td>{$g['id']}</td><td><a href=\"/gauge.php?id={$g['id']}\">$gname</a></td><td>$gloc</td><td>$gusgs</td></tr>\n";
    }
    echo '</table>';
} else {
    echo '<p style="margin-top:1rem;color:#666">No associated gauges.</p>';
}

echo '<p style="margin-top:1rem"><a href="/index.html">Back to main page</a></p>';

include_footer();
