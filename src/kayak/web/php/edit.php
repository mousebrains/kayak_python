<?php
declare(strict_types=1);
/**
 * Reach/gauge editing form + submission — maintainer-only.
 *
 * GET  /edit.php?id=<id>[&type=reach|gauge]   — show the edit form
 * POST /edit.php?id=<id>[&type=reach|gauge]   — save changes
 *
 * Auth: ed_sess editor-session cookie + ed_csrf double-submit cookie
 * (same pattern as /review.php). A signed-in editor without
 * status='maintainer' gets 403; anonymous visitors get bounced to
 * /login.php via require_maintainer(). propose.php routes maintainers
 * here automatically for the reach path so there's a single canonical
 * direct-edit path.
 *
 * SA-lite (dataset-separation D1): saving no longer writes the live
 * table — the changed fields are frozen as a self-endorsed
 * change_request (status 'approved') that the review queue renders with
 * land-it-as-a-kayak_data-PR instructions; the dataset repo is the only
 * metadata authority. (Pre-SA-lite saves wrote the table directly and
 * logged edit_history rows.)
 *
 * Type defaults to 'reach' for backward compatibility; 'gauge' edits
 * gauge-metadata fields (location, coordinates, elevation, etc.).
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/pubhash_request.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';
require_once __DIR__ . '/includes/editor_bridge.php';

// Reach/gauge coordinate columns are Numeric(9,6) — values are rounded to this
// 6-dp scale before freezing so a pasted full-precision lat/lon can't produce a
// dataset PR that validate-dataset rejects ("decimal places exceeds scale 6").
const COORD_FIELDS = [
    'latitude', 'longitude',
    'latitude_start', 'longitude_start', 'latitude_end', 'longitude_end',
];

$maintainer = require_maintainer();

$type = $_GET['type'] ?? $_POST['target_type'] ?? 'reach';
if (!in_array($type, ['reach', 'gauge'], true)) {
    http_response_code(400);
    exit('Unsupported edit target type');
}

$id_get   = pubhash_param_id();
$id_reach = filter_input(INPUT_POST, 'reach_id', FILTER_VALIDATE_INT);
$id_gauge = filter_input(INPUT_POST, 'gauge_id', FILTER_VALIDATE_INT);
$id = (is_int($id_get) && $id_get !== 0) ? $id_get
    : ((is_int($id_reach) && $id_reach !== 0) ? $id_reach : $id_gauge);
if (!is_int($id) || $id < 1) { http_response_code(400); exit('Missing id parameter'); }

$db = get_db();

if ($type === 'reach') {
    $row = get_reach_or_404($id);
    $name = ($row['display_name'] ?? '') !== '' ? $row['display_name'] : $row['name'];
    $table = 'reach';
    $editable_fields = [
        'display_name', 'sort_name', 'description', 'difficulties',
        'basin', 'region', 'length', 'gradient', 'elevation_lost',
        'season', 'scenery', 'features', 'remoteness', 'nature',
        'watershed_type', 'optimal_flow', 'notes',
    ];
    $numeric_fields = ['length', 'gradient', 'elevation_lost', 'optimal_flow'];
    $textarea_fields = ['description', 'difficulties', 'features', 'notes'];
    $back_url = pubhash_url('description', $id);
    $back_label = 'View description';
} else { // gauge
    $stmt = $db->prepare('SELECT * FROM gauge WHERE id = ?');
    $stmt->execute([$id]);
    $row = $stmt->fetch();
    if ($row === false) { http_response_code(404); exit('Gauge not found'); }
    $name = $row['name'];
    $table = 'gauge';
    $editable_fields = [
        'name', 'location',
        'latitude', 'longitude', 'elevation', 'drainage_area',
        'bank_full', 'flood_stage', 'huc',
        'station_id', 'usgs_id', 'cbtt_id', 'geos_id', 'nws_id', 'nwsli_id', 'snotel_id',
    ];
    $numeric_fields = [
        'latitude', 'longitude', 'elevation', 'drainage_area',
        'bank_full', 'flood_stage',
    ];
    $textarea_fields = [];
    $back_url = pubhash_url('gauge', $id);
    $back_label = 'View gauge';
}

// -----------------------------------------------------------------------
// POST — apply changes + log diff to edit_history
// -----------------------------------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();

    // Compare each submitted editable field against the current row; only
    // changed values get into the frozen diff. Empty submissions are
    // skipped (matches the legacy behavior — clearing a field requires a
    // separate flow that this form doesn't offer yet).
    $changes = [];
    foreach ($editable_fields as $field) {
        if (!isset($_POST[$field])) continue;
        $val = trim($_POST[$field]);
        if ($val === '') continue;

        if (in_array($field, $numeric_fields, true)) {
            if (!is_numeric($val)) continue;
            $val = (float)$val;
            // Coordinate columns are Numeric(9,6) — round to the 6-dp scale so a
            // pasted full-precision lat/lon (e.g. 14 dp from a map) doesn't freeze
            // a value validate-dataset rejects (the worker writes str(float)).
            if (in_array($field, COORD_FIELDS, true)) {
                $val = round($val, 6);
            }
        }

        $old = $row[$field] ?? null;
        $old_str = (string)$old;
        if ($old_str === (string)$val) continue;

        // old_str is the current (T2) value as a string; the TOCTOU guard below
        // compares it to the form's load-time base without re-casting the PDO row.
        $changes[$field] = ['old' => $old, 'new' => $val, 'old_str' => $old_str];
    }

    // TOCTOU guard: each field carries the value shown when the form loaded
    // (base_<field>). If the dataset changed a field being submitted since then
    // (a deploy's sync-metadata, or another bridge merge), the maintainer's view
    // is stale — endorsing would let the worker overwrite a change they never saw
    // (the drift base is captured here at submit time). Reject a stale view; fail
    // closed if a base is missing (e.g. a stale form).
    //
    // Same accepted residual window as the review path: this compares against the
    // $row loaded above (outside the freeze transaction); bridge_capture_base
    // re-reads inside it. A sub-millisecond, deploy-only (sync-metadata) gap that
    // the downstream human-reviewed kayak_data PR also backstops. The original
    // minutes-long render→submit window is what's closed here.
    foreach ($changes as $field => $pair) {
        $rendered = $_POST['base_' . $field] ?? null;
        if (!is_string($rendered) || $rendered !== $pair['old_str']) {
            http_response_code(409);
            exit("The $type changed since you opened this form — reload and redo your edit.");
        }
    }

    // SA-lite (dataset-separation D1): a direct edit no longer writes the
    // live table — the dataset repo is the only metadata authority, and a
    // direct write would be silently reverted by the next deploy's
    // sync-metadata. Instead, freeze the diff as a self-endorsed
    // change_request (status 'approved'), which shows up in the review
    // queue with the same land-it-as-a-kayak_data-PR instructions as an
    // endorsed editor proposal.
    $cr_id = null;
    if ($changes !== []) {
        $frozen = [];
        $carried_base = [];
        foreach ($changes as $field => $pair) {
            $frozen[$field] = $pair['new'];
            // Render-time base for the in-transaction TOCTOU verify (PR #219).
            if (isset($_POST['base_' . $field]) && is_string($_POST['base_' . $field])) {
                $carried_base[$field] = $_POST['base_' . $field];
            }
        }
        $applied = [$type => $frozen];
        // JSON_PRESERVE_ZERO_FRACTION: numeric fields are floats, so a whole-number
        // value keeps its ".0" — the worker writes str(float), matching the
        // dataset's canonical numeric form (consistent with the review path, M3).
        $payload = json_encode($applied, JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);
        $payload_str = $payload !== false ? $payload : '{}';
        // Freeze the self-endorsed diff and queue its bridge row in ONE
        // transaction (Tier 2, docs/PLAN_editor_pr_bridge.md): a direct edit is
        // immediately 'approved', so it joins the same kayak_data-PR queue as an
        // endorsed editor proposal.
        $maint_id = (int)$maintainer['id'];
        $db->beginTransaction();
        try {
            $db->prepare(
                "INSERT INTO change_request
                   (target_type, target_id, editor_id, subject, payload_json,
                    status, submitted_at, reviewed_at, reviewed_by, applied_json)
                 VALUES (?, ?, ?, ?, ?, 'approved', datetime('now'), datetime('now'), ?, ?)"
            )->execute([
                $type,
                $id,
                $maint_id,
                'Direct edit: ' . (string)$name,
                $payload_str,
                $maint_id,
                $payload_str,
            ]);
            $cr_id = (int)$db->lastInsertId();
            // $carried_base is verified == the live row inside this transaction;
            // a row that drifted since the form loaded throws and rolls back.
            bridge_enqueue($db, $cr_id, $type, $id, $applied, $payload_str, $maint_id, $carried_base);
            $db->commit();
        } catch (BridgeBaseDriftException $e) {
            if ($db->inTransaction()) {
                $db->rollBack();
            }
            error_log('edit.php: base drift — ' . $e->getMessage());
            http_response_code(409);
            exit("The $type changed since you opened this form — reload and redo your edit.");
        } catch (Throwable $e) {
            if ($db->inTransaction()) {
                $db->rollBack();
            }
            error_log('edit.php: freeze failed: ' . $e->getMessage());
            http_response_code(500);
            exit('Save failed — see server log.');
        }
    }

    header('Cache-Control: no-cache');
    include_header('Changes Frozen for Data Review', '', '', '', ['type' => $type, 'id' => $id]);
    echo '<h2>Changes Frozen for Data Review</h2>';
    if ($changes !== [] && $cr_id !== null) {
        $n = count($changes);
        $fields = implode(', ', array_keys($changes));
        echo '<p>Froze ' . $n . ' field' . ($n === 1 ? '' : 's')
           . ' for <strong>' . htmlspecialchars($name) . '</strong>: '
           . '<code>' . htmlspecialchars($fields) . '</code></p>';
        echo '<p style="padding:.5rem;background:#fff8e1;border:1px solid #e6d28a;border-radius:4px">'
           . 'Nothing changed on the live site yet: the dataset repo is the only '
           . 'metadata authority. Land the frozen diff as a <code>kayak_data</code> PR, '
           . 'then mark <a href="/review.php?id=' . $cr_id . '">request #' . $cr_id . '</a> '
           . 'resolved once the deploy ships it.</p>';
    } else {
        echo '<p>No changes to save for <strong>' . htmlspecialchars($name) . '</strong>.</p>';
    }
    echo '<p><a href="' . htmlspecialchars($back_url) . '">' . htmlspecialchars($back_label) . '</a>';
    echo ' | <a href="/index.html">Back to main page</a></p>';
    include_footer();
    exit;
}

// -----------------------------------------------------------------------
// GET — show the form
// -----------------------------------------------------------------------
header('Cache-Control: no-cache');
include_header("Edit $name", '', '', '', ['type' => $type, 'id' => $id]);

echo '<h2>Edit: ' . htmlspecialchars($name) . '</h2>';
$action = pubhash_url('edit', $id, $type === 'gauge' ? '&amp;type=gauge' : '');
echo '<form method="POST" action="' . $action . '" class="edit-form">';
echo '<input type="hidden" name="target_type" value="' . htmlspecialchars($type) . '">';
$id_field = $type === 'gauge' ? 'gauge_id' : 'reach_id';
echo '<input type="hidden" name="' . $id_field . '" value="' . $id . '">';
echo '<input type="hidden" name="csrf_token" value="' . htmlspecialchars(csrf_token()) . '">';

foreach ($editable_fields as $field) {
    $val = htmlspecialchars((string)($row[$field] ?? ''));
    $label = ucwords(str_replace('_', ' ', $field));
    echo "<label>$label</label>";

    // Carry the load-time value so POST can reject if the dataset changed this
    // field since the form opened (TOCTOU guard).
    echo "<input type=\"hidden\" name=\"base_$field\" value=\"$val\">";
    if (in_array($field, $textarea_fields, true)) {
        echo "<textarea name=\"$field\">$val</textarea>";
    } else {
        echo "<input type=\"text\" name=\"$field\" value=\"$val\">";
    }
}

echo '<button type="submit">Save Changes</button>';
echo '</form>';
echo '<p style="margin-top:1rem"><a href="' . htmlspecialchars($back_url) . '">Cancel</a></p>';

include_footer();
