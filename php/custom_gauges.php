<?php

declare(strict_types=1);

/**
 * Custom gauges page — renders a gauges table for arbitrary gauge IDs.
 *
 * URL format: /custom_gauges.php?ids=1,2,3  (bookmarkable, shareable)
 *
 * Thin orchestration shim — arg-parse + empty-redirect + dispatch.
 * Everything else lives in includes/custom_gauges_handler.php
 * (Tier 5.C.3 of docs/done/PLAN_php_layer_split.md).
 */

require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/custom_gauges_handler.php';

$raw = (string)(filter_input(INPUT_GET, 'ids', FILTER_DEFAULT) ?? '');
$ids = array_values(array_unique(array_filter(
    array_map('intval', explode(',', $raw)),
    fn($v) => $v > 0,
)));

if (!$ids) {
    header('Location: /gauge_picker.php');
    exit;
}

// Cap at 200 (matches custom.php — keeps memory + AJAX response size sane).
$ids = array_slice($ids, 0, 200);

handle_custom_gauges(get_db(), $ids, $raw);
