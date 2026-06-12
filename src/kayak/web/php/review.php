<?php

declare(strict_types=1);

/**
 * Maintainer moderation page for change_request proposals.
 *
 * GET  /review.php                 List pending
 * GET  /review.php?id=N            Detail + diff + editable form
 * POST /review.php  action=approve Endorse: freeze the (possibly tweaked)
 *                                  payload in applied_json for data review
 *                                  (SA-lite, D1 — nothing is applied; the
 *                                  change lands via a kayak_data PR + deploy)
 * POST /review.php  action=reject  Mark rejected + optional reviewer note
 * POST /review.php  action=reply   Send reply, keep pending
 * POST /review.php  action=reply_and_close  Reply + mark resolved
 * POST /review.php  action=resolve Mark resolved (pending, or an endorsed
 *                                  request once its dataset change deploys)
 *
 * Thin orchestration shim — feature-gate + maintainer-auth + dispatch.
 * Everything else lives in includes/review_handler.php (Tier 5.R.2
 * of docs/done/PLAN_php_layer_split.md).
 */

require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/review_handler.php';

require_editor_feature();
$maint = require_maintainer();

handle_review_request(get_db(), $maint);
