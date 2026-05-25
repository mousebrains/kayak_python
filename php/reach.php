<?php
declare(strict_types=1);
/**
 * Reach browser — view reach details with navigation.
 *
 * Usage:
 *   /reach.php?id=<reach_id>     detail mode
 *   /reach.php?q=<search-term>   search mode (single match auto-redirects)
 *   /reach.php?st=<state>        state-filter mode (combines with ?q=)
 *   /reach.php                   default to first reach by sort_name
 *   /reach.php?hidden=1          show no_show=1 reaches in any of the above
 *
 * Mode-dispatch only; logic lives in:
 *   includes/reach_search.php  → handle_search_mode (?q= / ?st=)
 *   includes/reach_detail.php  → handle_reach_detail (?id= / default)
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/reach_search.php';
require_once __DIR__ . '/includes/reach_detail.php';

$db = get_db();

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

$id_raw = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT);
$q_raw  = filter_input(INPUT_GET, 'q', FILTER_DEFAULT);
$st_raw = filter_input(INPUT_GET, 'st', FILTER_DEFAULT);
$hidden_raw = filter_input(INPUT_GET, 'hidden', FILTER_VALIDATE_INT);

$q = is_string($q_raw) ? $q_raw : '';
$st = is_string($st_raw) && $st_raw !== '' ? strtoupper(trim($st_raw)) : '';
$hidden = ($hidden_raw === 1) ? 1 : 0;

// --- Search mode ---
$q_trimmed = trim($q);
if ($q_trimmed !== '' || $st !== '') {
    handle_search_mode($db, $q_trimmed, $st, $hidden, $compact_css);
}

// --- Default: show first reach ---
$id = is_int($id_raw) && $id_raw > 0 ? $id_raw : null;
if ($id === null) {
    $row = $db->prepare('SELECT id FROM reach WHERE no_show = ? ORDER BY sort_name, id ASC LIMIT 1');
    $row->execute([$hidden]);
    $row = $row->fetch();
    if ($row === false) {
        header('Cache-Control: no-cache');
        include_header('Reaches');
        echo '<p>No reaches in database.</p>';
        include_footer();
        exit;
    }
    $id = (int)$row['id'];
}

handle_reach_detail($db, $id, $hidden, $q, $st, $compact_css);
