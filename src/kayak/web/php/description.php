<?php
declare(strict_types=1);
/**
 * Reach description page — readings, plots, map link, metadata.
 *
 * Usage: /description.php?id=<reach_id>[&start=YYYY-MM-DD&end=YYYY-MM-DD][&hidden=1]
 *
 * 400 on missing/invalid id. Single-mode entry point — every request
 * is a detail render via handle_description_detail in
 * includes/description_detail.php.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/validate.php';
require_once __DIR__ . '/includes/pubhash_request.php';
require_once __DIR__ . '/includes/description_detail.php';

// 301 a legacy ?id= to the canonical ?h=, then resolve.
pubhash_redirect_legacy_id();
$id = pubhash_param_id();
if ($id === null) {
    http_response_code(400);
    exit('Missing id parameter');
}

$start_raw = filter_input(INPUT_GET, 'start', FILTER_SANITIZE_SPECIAL_CHARS);
$end_raw   = filter_input(INPUT_GET, 'end', FILTER_SANITIZE_SPECIAL_CHARS);
$start_date = validate_date(is_string($start_raw) ? $start_raw : null);
$end_date   = validate_date(is_string($end_raw) ? $end_raw : null);

$hidden_raw = filter_input(INPUT_GET, 'hidden', FILTER_VALIDATE_INT);
$hidden = ($hidden_raw === 1) ? 1 : 0;

handle_description_detail(get_db(), $id, $start_date, $end_date, $hidden);
