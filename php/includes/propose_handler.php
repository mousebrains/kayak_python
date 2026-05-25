<?php

declare(strict_types=1);

/**
 * Editor proposal handler for /propose.php — GET renders the form,
 * POST validates + upserts a change_request row.
 *
 * Called from propose.php after feature-flag + editor-feature checks
 * and the maintainer-skip-to-/edit.php redirect. Assumes $ed is a
 * valid signed-in editor (non-maintainer or maintainer-with-edge-case).
 *
 * Convention matches the other helpers in this directory: function-only
 * (no top-level side effects beyond require_once), snake_case names,
 * strict types, helpers prefixed with `_` are file-private.
 */

require_once __DIR__ . '/db.php';
require_once __DIR__ . '/auth.php';
require_once __DIR__ . '/mail.php';
require_once __DIR__ . '/sanity.php';
require_once __DIR__ . '/source_url.php';
require_once __DIR__ . '/header.php';
require_once __DIR__ . '/footer.php';

/** Per-editor daily change_request cap, by editor.status. */
const PROPOSE_DAILY_CAPS = ['pending' => 3, 'minimal' => 10, 'full' => 20, 'maintainer' => 9999];

/**
 * Dispatch the propose flow and write the full HTTP response.
 *
 * 400 on type !== 'reach' (Phase 2 only supports reach proposals).
 * 400 on missing/invalid id. Otherwise loads reach + context, runs
 * the POST validate + upsert if applicable, then renders the form.
 *
 * @param array<string, mixed> $ed  Signed-in editor row from current_editor()
 */
function handle_propose(PDO $db, array $ed, string $type, int $id): void
{
    if ($type !== 'reach') {
        http_response_code(400);
        exit('Only reach proposals supported in Phase 2.');
    }
    if ($id <= 0) {
        http_response_code(400);
        exit('Missing id parameter');
    }

    $reach = get_reach_or_404($id);
    $reach_name = ($reach['display_name'] ?? '') !== ''
        ? $reach['display_name']
        : (($reach['name'] ?? '') !== '' ? $reach['name'] : "Reach #$id");

    $ctx = _load_propose_context($db, $ed, $id);

    $errors = [];
    $warnings = [];
    $saved = false;

    if ($_SERVER['REQUEST_METHOD'] === 'POST') {
        [$errors, $warnings, $saved] = _handle_propose_post(
            $db, $ed, $reach, $reach_name, $id, $ctx
        );
    }

    _render_propose_form($reach, $reach_name, $id, $ed, $ctx, $errors, $warnings, $saved);
}

/**
 * Load propose-form context: editor's tier-gated field set, the editor's
 * existing pending proposal for this reach (if any — we update in place
 * rather than stacking), current class/range for prefill + diffing, and
 * daily-cap accounting.
 *
 * @param array<string, mixed> $ed
 * @return array{
 *     tier: string,
 *     reach_fields: list<string>,
 *     allow_full: bool,
 *     existing: array<string, mixed>|null,
 *     cap: int,
 *     submitted_today: int,
 *     cur_classes: list<string>,
 *     cur_range: array{low: mixed, high: mixed, data_type: string}
 * }
 */
function _load_propose_context(PDO $db, array $ed, int $id): array
{
    $tier = (string)$ed['status'];
    $text_fields = ['description', 'features'];
    $full_reach_fields = [
        'display_name',
        'latitude_start', 'longitude_start',
        'latitude_end',   'longitude_end',
    ];
    $allow_full = in_array($tier, ['full', 'maintainer'], true);
    $reach_fields = $allow_full ? array_merge($text_fields, $full_reach_fields) : $text_fields;

    $cur_classes = [];
    $cur_range = ['low' => null, 'high' => null, 'data_type' => 'flow'];
    $st = $db->prepare(
        'SELECT name, low, low_data_type, high, high_data_type
         FROM reach_class WHERE reach_id = ? ORDER BY id'
    );
    $st->execute([$id]);
    foreach ($st->fetchAll() as $row) {
        $cur_classes[] = $row['name'];
        // First row with populated bounds wins as the primary range.
        if ($cur_range['low'] === null && $cur_range['high'] === null
            && ($row['low'] !== null || $row['high'] !== null)) {
            $cur_range = [
                'low'       => $row['low'],
                'high'      => $row['high'],
                'data_type' => ($row['low_data_type'] ?? '') !== ''
                    ? $row['low_data_type']
                    : (($row['high_data_type'] ?? '') !== '' ? $row['high_data_type'] : 'flow'),
            ];
        }
    }

    $existing_stmt = $db->prepare(
        "SELECT * FROM change_request
         WHERE editor_id = ? AND target_type = 'reach' AND target_id = ? AND status = 'pending'
         ORDER BY submitted_at DESC LIMIT 1"
    );
    $existing_stmt->execute([$ed['id'], $id]);
    $existing_row = $existing_stmt->fetch();
    $existing = $existing_row === false ? null : $existing_row;

    $cap = PROPOSE_DAILY_CAPS[$tier] ?? 3;
    $count_stmt = $db->prepare(
        "SELECT COUNT(*) FROM change_request
         WHERE editor_id = ? AND submitted_at > datetime('now', '-1 day')"
    );
    $count_stmt->execute([$ed['id']]);
    $submitted_today = (int)$count_stmt->fetchColumn();

    return [
        'tier' => $tier,
        'reach_fields' => $reach_fields,
        'allow_full' => $allow_full,
        'existing' => $existing,
        'cap' => $cap,
        'submitted_today' => $submitted_today,
        'cur_classes' => $cur_classes,
        'cur_range' => $cur_range,
    ];
}

/**
 * Validate the POSTed form and upsert a change_request row. Returns
 * [errors, warnings, saved] — saved=true means the success banner
 * should replace the form. saved=true is also set on honeypot trip
 * (silently — bot thinks it worked).
 *
 * Behaviorally identical to the pre-extract inline POST handler
 * (lines 106-290 of pre-Tier-5.P propose.php).
 *
 * @param  array<string, mixed>                          $ed
 * @param  array<string, mixed>                          $reach
 * @param  array{
 *     tier: string, reach_fields: list<string>, allow_full: bool,
 *     existing: array<string, mixed>|null, cap: int, submitted_today: int,
 *     cur_classes: list<string>,
 *     cur_range: array{low: mixed, high: mixed, data_type: string}
 * } $ctx
 * @return array{0: list<string>, 1: list<string>, 2: bool}  [errors, warnings, saved]
 */
function _handle_propose_post(PDO $db, array $ed, array $reach, string $reach_name, int $id, array $ctx): array
{
    require_csrf();

    $errors = [];
    $warnings = [];
    $saved = false;

    // Honeypot — silently accept, save nothing.
    if (($_POST['website'] ?? '') !== '') {
        return [[], [], true];
    }

    if ($ctx['submitted_today'] >= $ctx['cap'] && $ctx['existing'] === null) {
        return [["Daily submission cap of {$ctx['cap']} reached. Please try again later."], [], false];
    }

    $proposed_reach = [];
    foreach ($ctx['reach_fields'] as $f) {
        if (!array_key_exists($f, $_POST)) {
            continue;
        }
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

    $issues = [];
    if (isset($proposed_reach['display_name'])) {
        $issues = array_merge($issues,
            check_display_name((string)$proposed_reach['display_name'], $reach['river']));
    }
    foreach (['description' => 10000, 'features' => 5000] as $f => $max) {
        if (isset($proposed_reach[$f])) {
            $issues = array_merge($issues, check_text_length($f, (string)$proposed_reach[$f], $max));
        }
    }
    if ($ctx['allow_full']) {
        $ref_lat = isset($reach['latitude'])  ? (float)$reach['latitude']  : null;
        $ref_lon = isset($reach['longitude']) ? (float)$reach['longitude'] : null;
        // Coordinate fields only ever hold float|null here (the loop above
        // rejects non-numeric input), but $proposed_reach is a heterogeneous
        // array<string, float|string|null>; narrow to float|null for the
        // coordinate validators.
        $lat_s = $proposed_reach['latitude_start']  ?? null;
        $lon_s = $proposed_reach['longitude_start'] ?? null;
        $lat_e = $proposed_reach['latitude_end']    ?? null;
        $lon_e = $proposed_reach['longitude_end']   ?? null;
        assert(!is_string($lat_s) && !is_string($lon_s) && !is_string($lat_e) && !is_string($lon_e));
        $issues = array_merge($issues, check_coords('put-in', $lat_s, $lon_s, $ref_lat, $ref_lon));
        $issues = array_merge($issues, check_coords('take-out', $lat_e, $lon_e, $ref_lat, $ref_lon));
        $issues = array_merge($issues, check_putin_takeout($lat_s, $lon_s, $lat_e, $lon_e));
    }

    $proposed_class_payload = null;
    if ($ctx['allow_full'] && isset($_POST['classes_present'])) {
        $raw = trim((string)($_POST['classes'] ?? ''));
        $names = $raw === '' ? [] : array_values(array_filter(array_map('trim', explode(',', $raw)), fn($s) => $s !== ''));
        foreach ($names as $c) {
            $issues = array_merge($issues, check_class_string('class', $c));
        }
        $lo = trim((string)($_POST['flow_low']  ?? ''));
        $hi = trim((string)($_POST['flow_high'] ?? ''));
        $dt = trim((string)($_POST['flow_data_type'] ?? 'flow'));
        $range = [
            'low'       => $lo !== '' ? (float)$lo : null,
            'high'      => $hi !== '' ? (float)$hi : null,
            'data_type' => $dt !== '' ? $dt : 'flow',
        ];
        $issues = array_merge($issues, check_flow_range($range['low'], $range['high'], $range['data_type']));
        $proposed_class_payload = ['names' => $names, 'range' => $range];
    }

    $notes = strip_html_tags(trim((string)($_POST['notes_to_maint'] ?? '')));
    $issues = array_merge($issues, check_text_length('notes_to_maint', $notes, 5000));

    $errors = array_map(fn($i) => $i['message'], sanity_errors($issues));
    $warnings = array_map(fn($i) => $i['message'], sanity_warnings($issues));

    if ($errors !== []) {
        return [$errors, $warnings, false];
    }

    // Build diff-only payload.
    $payload = [];
    foreach ($proposed_reach as $f => $v) {
        $cur = $reach[$f] ?? null;
        if ($cur === null) {
            $cur = '';
        }
        if ((string)$cur !== (string)$v) {
            $payload['reach'][$f] = $v;
        }
    }
    if ($proposed_class_payload !== null) {
        $cur_range_tuple = [$ctx['cur_range']['low'], $ctx['cur_range']['high'], $ctx['cur_range']['data_type']];
        $new_range_tuple = [
            $proposed_class_payload['range']['low'],
            $proposed_class_payload['range']['high'],
            $proposed_class_payload['range']['data_type'],
        ];
        if ($proposed_class_payload['names'] !== $ctx['cur_classes']
            || $cur_range_tuple !== $new_range_tuple) {
            $payload['reach_class'] = $proposed_class_payload;
        }
    }

    if ($payload === [] && $notes === '') {
        return [
            ['No changes detected. Edit at least one field or add a note to the maintainer.'],
            $warnings,
            false,
        ];
    }

    $payload_json = json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    $subject = "Proposed edit: $reach_name";
    $src = sanitize_source_url((string)($_POST['source_url'] ?? ''));

    if ($ctx['existing'] !== null) {
        $db->prepare(
            "UPDATE change_request
             SET payload_json = ?, notes_to_maint = ?, subject = ?,
                 submitted_at = datetime('now'), source_url = ?
             WHERE id = ?"
        )->execute([$payload_json, $notes, $subject, $src !== '' ? $src : null, $ctx['existing']['id']]);
        $cr_id = (int)$ctx['existing']['id'];
    } else {
        $db->prepare(
            "INSERT INTO change_request
             (target_type, target_id, editor_id, submitted_at,
              subject, payload_json, notes_to_maint, status, source_url)
             VALUES ('reach', ?, ?, datetime('now'), ?, ?, ?, 'pending', ?)"
        )->execute([$id, $ed['id'], $subject, $payload_json, $notes, $src !== '' ? $src : null]);
        $cr_id = (int)$db->lastInsertId();
    }

    _send_proposal_notification($ed, $reach, $reach_name, $cr_id, $payload, $ctx, $notes, $src);

    return [[], $warnings, true];
}

/**
 * Email the maintainer(s) about a new or updated proposal. Returns
 * void — propose.php proceeds whether or not delivery succeeded
 * (mail failures are logged but don't block the user's save).
 *
 * @param array<string, mixed> $ed
 * @param array<string, mixed> $reach
 * @param array<string, mixed> $payload     The diff-only payload that landed in change_request
 * @param array{
 *     tier: string, reach_fields: list<string>, allow_full: bool,
 *     existing: array<string, mixed>|null, cap: int, submitted_today: int,
 *     cur_classes: list<string>,
 *     cur_range: array{low: mixed, high: mixed, data_type: string}
 * } $ctx
 */
function _send_proposal_notification(
    array $ed,
    array $reach,
    string $reach_name,
    int $cr_id,
    array $payload,
    array $ctx,
    string $notes,
    string $src,
): void {
    $maint_emails = maintainer_emails();
    if ($maint_emails === []) {
        return;
    }
    $site = rtrim(Config::str('site_url', 'https://levels.wkcc.org'), '/');
    $summary_lines = [];
    foreach (($payload['reach'] ?? []) as $f => $v) {
        $cur = (string)($reach[$f] ?? '');
        $preview = strlen((string)$v) > 120 ? substr((string)$v, 0, 117) . '...' : (string)$v;
        $cur_preview = strlen($cur) > 120 ? substr($cur, 0, 117) . '...' : $cur;
        $summary_lines[] = "$f:\n  old: $cur_preview\n  new: $preview";
    }
    if (isset($payload['reach_class'])) {
        $fmt_range = function (array $r): string {
            $lo = $r['low']  ?? null;
            $hi = $r['high'] ?? null;
            $dt = $r['data_type'] ?? 'flow';
            if ($lo === null && $hi === null) {
                return '(no range)';
            }
            return ($lo ?? '-') . ' to ' . ($hi ?? '-') . " $dt";
        };
        $new_names = $payload['reach_class']['names'];
        $new_range = $payload['reach_class']['range'];
        $summary_lines[] = 'reach_class: '
            . ($ctx['cur_classes'] === [] ? '(none)' : implode(', ', $ctx['cur_classes']))
            . ' -> '
            . ($new_names === [] ? '(none)' : implode(', ', $new_names));
        $summary_lines[] = 'flow range: '
            . $fmt_range($ctx['cur_range']) . ' -> ' . $fmt_range($new_range);
    }
    $summary = $summary_lines !== []
        ? implode("\n\n", $summary_lines)
        : '(No reach-column changes — only a note to the maintainer.)';

    $body = render_maintainer_notification(
        $reach_name,
        (string)$ed['email'],
        $summary,
        $notes,
        "$site/review.php?id=$cr_id",
        $src
    );
    foreach ($maint_emails as $to) {
        send_email($to, "[levels] proposed edit: $reach_name", $body);
    }
}

/**
 * Render the form (or the post-save success banner). All state is read
 * from the args — no global $_POST / $_SERVER reads beyond what the
 * source_url helper does internally.
 *
 * @param array<string, mixed> $reach
 * @param array<string, mixed> $ed
 * @param array{
 *     tier: string, reach_fields: list<string>, allow_full: bool,
 *     existing: array<string, mixed>|null, cap: int, submitted_today: int,
 *     cur_classes: list<string>,
 *     cur_range: array{low: mixed, high: mixed, data_type: string}
 * } $ctx
 * @param list<string> $errors
 * @param list<string> $warnings
 */
function _render_propose_form(
    array $reach,
    string $reach_name,
    int $id,
    array $ed,
    array $ctx,
    array $errors,
    array $warnings,
    bool $saved,
): void {
    $existing = $ctx['existing'];
    $tier = $ctx['tier'];
    $cur_classes = $ctx['cur_classes'];
    $cur_range = $ctx['cur_range'];
    $allow_full = $ctx['allow_full'];
    $cap = $ctx['cap'];
    $submitted_today = $ctx['submitted_today'];

    $csrf = htmlspecialchars(csrf_token());
    $source_url = $_SERVER['REQUEST_METHOD'] === 'POST'
        ? sanitize_source_url((string)($_POST['source_url'] ?? ''))
        : source_url_from_referrer('/propose.php');

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
  <?php if ($errors !== []): ?>
    <ul style="padding:.6rem 1.4rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px">
      <?php foreach ($errors as $e): ?><li><?= htmlspecialchars($e) ?></li><?php endforeach ?>
    </ul>
  <?php endif ?>
  <?php if ($warnings !== []): ?>
    <ul style="padding:.6rem 1.4rem;background:#fff4dc;border:1px solid #e8d291;border-radius:4px">
      <?php foreach ($warnings as $w): ?><li><?= htmlspecialchars($w) ?></li><?php endforeach ?>
    </ul>
  <?php endif ?>

  <?php if ($existing !== null): ?>
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
    <input type="hidden" name="source_url" value="<?= htmlspecialchars($source_url) ?>">
    <input type="text" name="website" value="" tabindex="-1" autocomplete="off"
           style="position:absolute;left:-9999px;width:1px;height:1px" aria-hidden="true">

    <?php if ($allow_full): ?>
      <label>Display name</label>
      <input type="text" name="display_name"
             value="<?= htmlspecialchars(_propose_prefill($reach, $existing, 'display_name')) ?>">
      <p style="font-size:.75rem;color:var(--c-text-muted);margin:0">
        Must include the river name (<?= htmlspecialchars((string)$reach['river']) ?>).
        Prefixes like SF, NF, Upper, Oak Fork are fine.
      </p>
    <?php endif ?>

    <label>Description</label>
    <textarea name="description" style="height:10em"><?= htmlspecialchars(_propose_prefill($reach, $existing, 'description')) ?></textarea>

    <label>Features</label>
    <textarea name="features" style="height:6em"><?= htmlspecialchars(_propose_prefill($reach, $existing, 'features')) ?></textarea>

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
                <?php $sel = $cur_range['data_type'];
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

<?php include_footer();
}

/**
 * Prefill helper — used inside _render_propose_form's template. Pulls
 * the field's value from the editor's existing pending proposal if
 * present, otherwise from the current reach row.
 *
 * @param array<string, mixed>      $reach
 * @param array<string, mixed>|null $existing
 */
function _propose_prefill(array $reach, ?array $existing, string $field): string
{
    if ($existing !== null) {
        $decoded = json_decode((string)$existing['payload_json'], true);
        $payload = is_array($decoded) ? $decoded : [];
        if (isset($payload['reach'][$field])) {
            return (string)$payload['reach'][$field];
        }
    }
    return (string)($reach[$field] ?? '');
}
