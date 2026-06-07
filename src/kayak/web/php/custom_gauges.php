<?php

declare(strict_types=1);

/**
 * Custom gauges page — renders a gauges table for arbitrary gauge IDs.
 *
 * URL format: /custom_gauges.php?h=1e,2k,9  (base-62 handles; bookmarkable,
 * shareable). The legacy /custom_gauges.php?ids=1,2,3 decimal form 301s to it.
 *
 * Thin orchestration shim — arg-parse + empty-redirect + dispatch.
 * Everything else lives in includes/custom_gauges_handler.php
 * (Tier 5.C.3 of docs/done/PLAN_php_layer_split.md).
 */

require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/pubhash_request.php';
require_once __DIR__ . '/includes/custom_gauges_handler.php';

// Canonical list form is ?h=<handle,…>; 301 a legacy ?ids=<decimal,…> to it.
pubhash_redirect_legacy_ids();
$ids = pubhash_param_ids();

if ($ids === []) {
    header('Location: /gauge_picker.php');
    exit;
}

// Cap at 200 (matches custom.php — keeps memory + AJAX response size sane).
$ids = array_slice($ids, 0, 200);

// Canonical handle-CSV for the handler's "Edit selection" link back to the picker.
$handle_csv = implode(',', array_map('pubhash_encode', $ids));
handle_custom_gauges(get_db(), $ids, $handle_csv);
