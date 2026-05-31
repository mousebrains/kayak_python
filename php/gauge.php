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
require_once __DIR__ . '/includes/pubhash_request.php';
require_once __DIR__ . '/includes/gauge_search.php';
require_once __DIR__ . '/includes/gauge_detail.php';

$db = get_db();

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

// --- Detail mode: 301 a legacy ?id= to the canonical ?h=, then resolve ---
pubhash_redirect_legacy_id();
$id = pubhash_param_id();

// --- Default: show first gauge ---
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
