<?php

declare(strict_types=1);

/**
 * Custom levels page — renders a levels table for arbitrary reach IDs.
 *
 * URL format: /custom.php?ids=237,339,340  (bookmarkable, shareable)
 *
 * Thin orchestration shim — arg-parse + empty-redirect + dispatch.
 * Everything else lives in includes/custom_handler.php (Tier 5.C.2
 * of docs/done/PLAN_php_layer_split.md).
 */

require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/custom_handler.php';

$raw = filter_input(INPUT_GET, 'ids', FILTER_DEFAULT) ?? '';
$ids = array_values(array_unique(array_filter(
    array_map('intval', explode(',', $raw)),
    fn($v) => $v > 0,
)));

if (!$ids) {
    header('Location: /picker.php');
    exit;
}

// Cap at 200 reaches (500 caused OOM with 128MB limit due to sparkline queries).
$ids = array_slice($ids, 0, 200);

handle_custom_levels(get_db(), $ids);
