<?php
/**
 * Section description page.
 *
 * Usage: /description.php?id=<section_id>
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

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

if ($gauge) {
    // Data links
    echo '<tr><td>Data</td><td>';
    echo '<a href="/view.php?id=' . $id . '">Current readings</a>';
    echo ' | <a href="/plot.php?type=flow&id=' . $id . '">Flow plot</a>';
    echo ' | <a href="/plot.php?type=gage&id=' . $id . '">Gage plot</a>';
    echo '</td></tr>';
}

echo '</table>';
echo '<p style="margin-top:1rem"><a href="/index.html">Back to main page</a>';
echo ' | <a href="/edit.php?id=' . $id . '">Edit</a></p>';

include_footer();
