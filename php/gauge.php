<?php
declare(strict_types=1);
/**
 * Gauge browser — view gauge details with associated sources and reaches.
 *
 * Usage:
 *   /gauge.php?id=<gauge_id>    detail mode
 *   /gauge.php?q=<search-term>  search mode (single match auto-redirects)
 *   /gauge.php                  default to first gauge by id
 *
 * Mode-dispatch only; logic lives in:
 *   includes/gauge_search.php   → handle_gauge_search (?q=)
 *   includes/gauge_detail.php   → handle_gauge_detail (?id= / default)
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/validate.php';
require_once __DIR__ . '/includes/gauge_search.php';
require_once __DIR__ . '/includes/gauge_detail.php';

$db = get_db();

$id_raw = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$q_raw  = filter_input(INPUT_GET, 'q', FILTER_DEFAULT);
$start_raw = filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS);
$end_raw   = filter_input(INPUT_GET, 'end', FILTER_SANITIZE_SPECIAL_CHARS);

$start_date = validate_date(is_string($start_raw) ? $start_raw : null);
$end_date   = validate_date(is_string($end_raw) ? $end_raw : null);

// --- Search mode ---
$q_trimmed = is_string($q_raw) ? trim($q_raw) : '';
if ($q_trimmed !== '') {
    handle_gauge_search($db, $q_trimmed);
}

// --- Default: show first gauge ---
$id = is_int($id_raw) && $id_raw > 0 ? $id_raw : null;
if ($id === null) {
    $row = db_query($db, 'SELECT id FROM gauge ORDER BY id ASC LIMIT 1')->fetch();
    if ($row === false) {
        header('Cache-Control: no-cache');
        include_header('Gauges', '', '', '', ['picker_kind' => 'gauge']);
        echo '<p>No gauges in database.</p>';
        include_footer();
        exit;
    }
    $id = (int)$row['id'];
}

handle_gauge_detail($db, $id, $start_date, $end_date);
