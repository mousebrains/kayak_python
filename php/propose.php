<?php
declare(strict_types=1);
/**
 * Propose an edit to a reach (or other target). Feature-flagged.
 *
 * GET  /propose.php?type=reach&id=N    Show the tier-gated form
 * POST /propose.php                    Validate + upsert change_request
 *
 * A maintainer landing here is bounced to /edit.php for direct editing.
 * An anonymous visitor is bounced to /login.php.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/mail.php';
require_once __DIR__ . '/includes/sanity.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();
$ed = require_editor();

// Maintainers skip the queue — use the direct editor instead.
if (is_maintainer($ed)) {
    $tid = filter_input(INPUT_GET, 'id', FILTER_VALIDATE_INT)
        ?: filter_input(INPUT_POST, 'target_id', FILTER_VALIDATE_INT);
    if (($_GET['type'] ?? 'reach') === 'reach' && $tid) {
        header("Location: /edit.php?id=$tid");
        exit;
    }
}

$type = $_GET['type']    ?? $_POST['target_type'] ?? 'reach';
$id   = (int)($_GET['id'] ?? $_POST['target_id']  ?? 0);

if ($type !== 'reach') {
    http_response_code(400);
    exit('Only reach proposals supported in Phase 2.');
}
if ($id <= 0) {
    http_response_code(400);
    exit('Missing id parameter');
}

$db = get_db();
$reach = get_reach_or_404($id);
$reach_name = $reach['display_name'] ?: $reach['name'] ?: "Reach #$id";

// Tier-gated field set
$tier = $ed['status'];
$text_fields = ['description', 'features'];  // available to all tiers
$full_reach_fields = ['display_name',
                      'latitude_start', 'longitude_start',
                      'latitude_end',   'longitude_end'];
$allow_full = in_array($tier, ['full', 'maintainer'], true);
$reach_fields = $allow_full ? array_merge($text_fields, $full_reach_fields) : $text_fields;

// Current classes + shared flow range (for form prefill + diffing)
$cur_classes = [];
$cur_range = ['low' => null, 'high' => null, 'data_type' => 'flow'];
$st = $db->prepare(
    'SELECT name, low, low_data_type, high, high_data_type
     FROM reach_class WHERE reach_id = ? ORDER BY id'
);
$st->execute([$id]);
foreach ($st->fetchAll() as $row) {
    $cur_classes[] = $row['name'];
    // First row with populated bounds wins as the primary range
    if ($cur_range['low'] === null && $cur_range['high'] === null
        && ($row['low'] !== null || $row['high'] !== null)) {
        $cur_range = [
            'low'       => $row['low'],
            'high'      => $row['high'],
            'data_type' => $row['low_data_type'] ?: ($row['high_data_type'] ?: 'flow'),
        ];
    }
}

// Load existing pending proposal by this editor for this reach — we update
// in place rather than stacking duplicates.
$existing_stmt = $db->prepare(
    "SELECT * FROM change_request
     WHERE editor_id = ? AND target_type = 'reach' AND target_id = ? AND status = 'pending'
     ORDER BY submitted_at DESC LIMIT 1"
);
$existing_stmt->execute([$ed['id'], $id]);
$existing = $existing_stmt->fetch() ?: null;

// Per-editor daily cap (banned editors can't even reach here — auth drops them)
$DAILY_CAPS = ['pending' => 3, 'minimal' => 10, 'full' => 20, 'maintainer' => 9999];
$cap = $DAILY_CAPS[$tier] ?? 3;
$count_stmt = $db->prepare(
    "SELECT COUNT(*) FROM change_request
     WHERE editor_id = ? AND submitted_at > datetime('now', '-1 day')"
);
$count_stmt->execute([$ed['id']]);
$submitted_today = (int)$count_stmt->fetchColumn();

$errors = [];
$warnings = [];
$saved = false;

// -----------------------------------------------------------------------
// POST — validate + upsert
// -----------------------------------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();

    // Honeypot — silently accept, save nothing
    if (($_POST['website'] ?? '') !== '') {
        $saved = true;
        goto render;  // noqa — minimal control flow
    }

    if ($submitted_today >= $cap && !$existing) {
        $errors[] = "Daily submission cap of $cap reached. Please try again later.";
        goto render;
    }

    // Collect reach-level proposed values
    $proposed_reach = [];
    foreach ($reach_fields as $f) {
        if (!array_key_exists($f, $_POST)) continue;
        $v = trim((string)$_POST[$f]);
        if (in_array($f, ['latitude_start', 'longitude_start', 'latitude_end', 'longitude_end'], true)) {
            $v = $v === '' ? null : (is_numeric($v) ? (float)$v : false);
            if ($v === false) {
                $errors[] = ucwords(str_replace('_', ' ', $f)) . ' must be a number.';
                continue;
            }
        } else {
            $v = strip_html_tags($v);
        }
        $proposed_reach[$f] = $v;
    }

    // Sanity checks
    $issues = [];
    if (isset($proposed_reach['display_name'])) {
        $issues = array_merge($issues,
            check_display_name($proposed_reach['display_name'], $reach['river']));
    }
    foreach (['description' => 10000, 'features' => 5000] as $f => $max) {
        if (isset($proposed_reach[$f])) {
            $issues = array_merge($issues, check_text_length($f, $proposed_reach[$f], $max));
        }
    }
    if ($allow_full) {
        $ref_lat = isset($reach['latitude'])  ? (float)$reach['latitude']  : null;
        $ref_lon = isset($reach['longitude']) ? (float)$reach['longitude'] : null;
        $issues = array_merge($issues, check_coords(
            'put-in',
            $proposed_reach['latitude_start']  ?? null,
            $proposed_reach['longitude_start'] ?? null,
            $ref_lat, $ref_lon
        ));
        $issues = array_merge($issues, check_coords(
            'take-out',
            $proposed_reach['latitude_end']  ?? null,
            $proposed_reach['longitude_end'] ?? null,
            $ref_lat, $ref_lon
        ));
        $issues = array_merge($issues, check_putin_takeout(
            $proposed_reach['latitude_start']  ?? null,
            $proposed_reach['longitude_start'] ?? null,
            $proposed_reach['latitude_end']    ?? null,
            $proposed_reach['longitude_end']   ?? null
        ));
    }

    // Full tier only: reach_class names + shared flow range from form
    $proposed_class_payload = null;
    if ($allow_full && isset($_POST['classes_present'])) {
        $raw = trim((string)($_POST['classes'] ?? ''));
        $names = $raw === '' ? [] : array_values(array_filter(array_map('trim', explode(',', $raw))));
        foreach ($names as $c) {
            $issues = array_merge($issues, check_class_string('class', $c));
        }
        $lo = trim((string)($_POST['flow_low']  ?? ''));
        $hi = trim((string)($_POST['flow_high'] ?? ''));
        $dt = trim((string)($_POST['flow_data_type'] ?? 'flow'));
        $range = [
            'low'       => $lo !== '' ? (float)$lo : null,
            'high'      => $hi !== '' ? (float)$hi : null,
            'data_type' => $dt ?: 'flow',
        ];
        $issues = array_merge($issues, check_flow_range($range['low'], $range['high'], $range['data_type']));
        $proposed_class_payload = ['names' => $names, 'range' => $range];
    }

    $notes = strip_html_tags(trim((string)($_POST['notes_to_maint'] ?? '')));
    $issues = array_merge($issues, check_text_length('notes_to_maint', $notes, 5000));

    $errors = array_map(fn($i) => $i['message'], sanity_errors($issues));
    $warnings = array_map(fn($i) => $i['message'], sanity_warnings($issues));

    if (!$errors) {
        // Build diff-only payload
        $payload = [];
        foreach ($proposed_reach as $f => $v) {
            $cur = $reach[$f] ?? null;
            if ($cur === null) $cur = '';
            if ((string)$cur !== (string)$v) {
                $payload['reach'][$f] = $v;
            }
        }
        if ($proposed_class_payload !== null) {
            $cur_range_tuple = [$cur_range['low'], $cur_range['high'], $cur_range['data_type']];
            $new_range_tuple = [
                $proposed_class_payload['range']['low'],
                $proposed_class_payload['range']['high'],
                $proposed_class_payload['range']['data_type'],
            ];
            if ($proposed_class_payload['names'] !== $cur_classes
                || $cur_range_tuple !== $new_range_tuple) {
                $payload['reach_class'] = $proposed_class_payload;
            }
        }

        if (empty($payload) && $notes === '') {
            $errors[] = 'No changes detected. Edit at least one field or add a note to the maintainer.';
        } else {
            $payload_json = json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
            $subject = "Proposed edit: $reach_name";

            if ($existing) {
                $db->prepare(
                    "UPDATE change_request
                     SET payload_json = ?, notes_to_maint = ?, subject = ?,
                         submitted_at = datetime('now')
                     WHERE id = ?"
                )->execute([$payload_json, $notes, $subject, $existing['id']]);
                $cr_id = (int)$existing['id'];
            } else {
                $db->prepare(
                    "INSERT INTO change_request
                     (target_type, target_id, editor_id, submitted_at,
                      subject, payload_json, notes_to_maint, status)
                     VALUES ('reach', ?, ?, datetime('now'), ?, ?, ?, 'pending')"
                )->execute([$id, $ed['id'], $subject, $payload_json, $notes]);
                $cr_id = (int)$db->lastInsertId();
            }

            // Notify maintainer(s)
            $maint_emails = maintainer_emails();
            $site = rtrim(auth_env('SITE_URL') ?: 'https://levels.mousebrains.com', '/');
            $summary_lines = [];
            foreach (($payload['reach'] ?? []) as $f => $v) {
                $cur = (string)($reach[$f] ?? '');
                $preview = strlen((string)$v) > 120 ? substr((string)$v, 0, 117) . '...' : (string)$v;
                $cur_preview = strlen($cur) > 120 ? substr($cur, 0, 117) . '...' : $cur;
                $summary_lines[] = "$f:\n  old: $cur_preview\n  new: $preview";
            }
            if (isset($payload['reach_class'])) {
                $fmt_range = function(array $r): string {
                    $lo = $r['low']  ?? null;
                    $hi = $r['high'] ?? null;
                    $dt = $r['data_type'] ?? 'flow';
                    if ($lo === null && $hi === null) return '(no range)';
                    return ($lo ?? '-') . ' to ' . ($hi ?? '-') . " $dt";
                };
                $new_names = $payload['reach_class']['names'] ?? [];
                $new_range = $payload['reach_class']['range'] ?? ['low'=>null,'high'=>null,'data_type'=>'flow'];
                $summary_lines[] = 'reach_class: '
                    . (empty($cur_classes) ? '(none)' : implode(', ', $cur_classes))
                    . ' -> '
                    . (empty($new_names) ? '(none)' : implode(', ', $new_names));
                $summary_lines[] = 'flow range: '
                    . $fmt_range($cur_range) . ' -> ' . $fmt_range($new_range);
            }
            $summary = $summary_lines
                ? implode("\n\n", $summary_lines)
                : '(No reach-column changes — only a note to the maintainer.)';

            $body = render_maintainer_notification(
                $reach_name,
                (string)$ed['email'],
                $summary,
                $notes,
                "$site/review.php?id=$cr_id"
            );
            foreach ($maint_emails as $to) {
                send_email($to, "[levels] proposed edit: $reach_name", $body);
            }
            $saved = true;
        }
    }
}

render:

// Prefill values for the form
function _prefill(array $reach, ?array $existing, string $field): string {
    if ($existing !== null) {
        $payload = json_decode((string)$existing['payload_json'], true) ?: [];
        if (isset($payload['reach'][$field])) {
            return (string)$payload['reach'][$field];
        }
    }
    return (string)($reach[$field] ?? '');
}

$csrf = htmlspecialchars(csrf_token());

header('Cache-Control: no-store');
include_header(
    "Suggest an edit — $reach_name",
    '',
    '',
    '',
    ['type' => 'reach', 'id' => $id]
);
?>
<h2>Suggest an edit: <?= htmlspecialchars($reach_name) ?></h2>

<p style="font-size:.85rem;color:var(--c-text-muted)">
  Your account is <strong><?= htmlspecialchars($tier) ?></strong>;
  <?= $submitted_today ?> of <?= $cap ?> daily submissions used.
  <a href="/description.php?id=<?= $id ?>">Back to the reach description</a>.
</p>

<?php if ($saved): ?>
  <p style="padding:.6rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px">
    Thanks — your proposal was recorded. The maintainer will review it.
    <a href="/description.php?id=<?= $id ?>">Return to the reach</a>.
  </p>
<?php else: ?>
  <?php if ($errors): ?>
    <ul style="padding:.6rem 1.4rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px">
      <?php foreach ($errors as $e): ?><li><?= htmlspecialchars($e) ?></li><?php endforeach ?>
    </ul>
  <?php endif ?>
  <?php if ($warnings): ?>
    <ul style="padding:.6rem 1.4rem;background:#fff4dc;border:1px solid #e8d291;border-radius:4px">
      <?php foreach ($warnings as $w): ?><li><?= htmlspecialchars($w) ?></li><?php endforeach ?>
    </ul>
  <?php endif ?>

  <?php if ($existing): ?>
    <p style="font-size:.85rem;color:var(--c-text-muted)">
      You already have a pending proposal for this reach (submitted
      <?= htmlspecialchars((string)$existing['submitted_at']) ?>); submitting
      again will replace it.
    </p>
  <?php endif ?>

  <form method="POST" action="/propose.php" class="edit-form">
    <input type="hidden" name="csrf_token" value="<?= $csrf ?>">
    <input type="hidden" name="target_type" value="reach">
    <input type="hidden" name="target_id" value="<?= $id ?>">
    <input type="text" name="website" value="" tabindex="-1" autocomplete="off"
           style="position:absolute;left:-9999px;width:1px;height:1px" aria-hidden="true">

    <?php if ($allow_full): ?>
      <label>Display name</label>
      <input type="text" name="display_name"
             value="<?= htmlspecialchars(_prefill($reach, $existing, 'display_name')) ?>">
      <p style="font-size:.75rem;color:var(--c-text-muted);margin:0">
        Must include the river name (<?= htmlspecialchars((string)$reach['river']) ?>).
        Prefixes like SF, NF, Upper, Oak Fork are fine.
      </p>
    <?php endif ?>

    <label>Description</label>
    <textarea name="description" style="height:10em"><?= htmlspecialchars(_prefill($reach, $existing, 'description')) ?></textarea>

    <label>Features</label>
    <textarea name="features" style="height:6em"><?= htmlspecialchars(_prefill($reach, $existing, 'features')) ?></textarea>

    <?php if ($allow_full): ?>
      <fieldset style="border:1px solid var(--c-border);padding:.6rem;margin-top:.75rem">
        <legend>Classes and flow range</legend>
        <input type="hidden" name="classes_present" value="1">
        <label>Classes (comma-separated, e.g. <code>III, III+</code>)</label>
        <input type="text" name="classes"
               value="<?= htmlspecialchars(implode(', ', $cur_classes)) ?>">
        <table style="width:100%;font-size:.85rem;margin-top:.5rem">
          <tr><th>Low</th><th>High</th><th>Type</th></tr>
          <tr>
            <td><input type="number" step="any" name="flow_low"
                       value="<?= htmlspecialchars((string)($cur_range['low'] ?? '')) ?>"></td>
            <td><input type="number" step="any" name="flow_high"
                       value="<?= htmlspecialchars((string)($cur_range['high'] ?? '')) ?>"></td>
            <td>
              <select name="flow_data_type">
                <?php $sel = $cur_range['data_type'] ?? 'flow';
                foreach (['flow', 'gauge'] as $dt): ?>
                  <option value="<?= $dt ?>"<?= $sel === $dt ? ' selected' : '' ?>><?= $dt ?></option>
                <?php endforeach ?>
              </select>
            </td>
          </tr>
        </table>
        <p style="font-size:.8rem;color:var(--c-muted);margin-top:.35rem">
          Single range applied to every class above. The site derives "below range / in range / above range"
          bands from these bounds.
        </p>
      </fieldset>

      <fieldset style="border:1px solid var(--c-border);padding:.6rem;margin-top:.75rem">
        <legend>Put-in / Take-out</legend>
        <table style="width:100%;font-size:.85rem">
          <tr><th></th><th>Latitude</th><th>Longitude</th></tr>
          <?php foreach ([['Put-in', 'start'], ['Take-out', 'end']] as [$lbl, $suf]): ?>
            <tr>
              <td><?= $lbl ?></td>
              <td><input type="number" step="any" name="latitude_<?= $suf ?>"
                         value="<?= htmlspecialchars((string)($reach["latitude_$suf"] ?? '')) ?>"></td>
              <td><input type="number" step="any" name="longitude_<?= $suf ?>"
                         value="<?= htmlspecialchars((string)($reach["longitude_$suf"] ?? '')) ?>"></td>
            </tr>
          <?php endforeach ?>
        </table>
      </fieldset>
    <?php endif ?>

    <label style="margin-top:.75rem">Notes to maintainer</label>
    <textarea name="notes_to_maint" style="height:6em" placeholder="Anything the maintainer should know (source for the change, caveats, etc.)"><?= htmlspecialchars((string)($existing['notes_to_maint'] ?? '')) ?></textarea>

    <button type="submit" style="margin-top:.75rem">Submit proposal</button>
  </form>
<?php endif ?>

<?php include_footer(); ?>
