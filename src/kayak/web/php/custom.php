<?php

declare(strict_types=1);

/**
 * Custom levels page — renders a levels table for arbitrary reach IDs.
 *
 * URL format: /custom.php?h=4u,5x,1e  (base-62 handles; bookmarkable, shareable).
 * The legacy /custom.php?ids=237,339,340 decimal form 301s to the ?h= canonical.
 *
 * Thin orchestration shim — arg-parse + empty-redirect + dispatch.
 * Everything else lives in includes/custom_handler.php (Tier 5.C.2
 * of docs/done/PLAN_php_layer_split.md).
 */

require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/pubhash_request.php';
require_once __DIR__ . '/includes/custom_handler.php';

// Canonical list form is ?h=<handle,…>; 301 a legacy ?ids=<decimal,…> to it.
pubhash_redirect_legacy_ids();
$ids = pubhash_param_ids();

if ($ids === []) {
    header('Location: /picker.php');
    exit;
}

// Cap at 200 reaches (500 caused OOM with 128MB limit due to sparkline queries).
$ids = array_slice($ids, 0, 200);

handle_custom_levels(get_db(), $ids);
