<?php
declare(strict_types=1);
/**
 * Propose an edit to a reach. Feature-flagged + editor-gated.
 *
 * Usage:
 *   GET  /propose.php?type=reach&id=N   render the tier-gated form
 *   POST /propose.php                   validate + upsert change_request
 *
 * A maintainer landing here is bounced to /edit.php for direct editing.
 * An anonymous visitor is bounced to /login.php (via require_editor).
 *
 * Mode-dispatch only; logic lives in:
 *   includes/propose_handler.php → handle_propose($db, $ed, $type, $id)
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/propose_handler.php';

require_editor_feature();
$ed = require_editor();

$type = (string)($_GET['type'] ?? $_POST['target_type'] ?? 'reach');
$id   = (int)($_GET['id'] ?? $_POST['target_id'] ?? 0);

// Maintainers skip the queue — use the direct editor for reach edits.
if (is_maintainer($ed) && $type === 'reach' && $id > 0) {
    header("Location: /edit.php?id=$id");
    exit;
}

handle_propose(get_db(), $ed, $type, $id);
