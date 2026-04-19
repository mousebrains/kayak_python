<?php
declare(strict_types=1);
/**
 * Maintainer admin — editor management.
 *
 * Uses form-association attributes (no inline JS — CSP forbids it). One
 * tiny hidden form per editor row; per-row action buttons reference it
 * via the `form=` attribute. The surrounding bulk form handles multi-
 * select approve of pending editors.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();
$maint = require_maintainer();
$db = get_db();

$flash = null;
$flash_err = null;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();
    $action = $_POST['action'] ?? '';
    $id  = (int)($_POST['id']  ?? 0);
    $ids = array_map('intval', (array)($_POST['ids'] ?? []));
    $ids = array_values(array_filter($ids, fn($n) => $n > 0));

    switch ($action) {
        case 'bulk_approve':
            if ($ids) {
                $in = implode(',', array_fill(0, count($ids), '?'));
                $stmt = $db->prepare(
                    "UPDATE editor
                     SET status = 'minimal', reviewed_at = datetime('now'), reviewed_by = ?
                     WHERE id IN ($in) AND status = 'pending'"
                );
                $stmt->execute(array_merge([(int)$maint['id']], $ids));
                $flash = $stmt->rowCount() . ' editor(s) promoted pending -> minimal.';
            }
            break;

        case 'promote':
            $db->prepare(
                "UPDATE editor
                 SET status = 'full', reviewed_at = datetime('now'), reviewed_by = ?
                 WHERE id = ? AND status IN ('pending', 'minimal')"
            )->execute([(int)$maint['id'], $id]);
            $flash = "Promoted editor #$id to full.";
            break;

        case 'approve_minimal':
            $db->prepare(
                "UPDATE editor
                 SET status = 'minimal', reviewed_at = datetime('now'), reviewed_by = ?
                 WHERE id = ? AND status = 'pending'"
            )->execute([(int)$maint['id'], $id]);
            $flash = "Approved editor #$id as minimal.";
            break;

        case 'demote':
            $db->prepare(
                "UPDATE editor
                 SET status = 'minimal', reviewed_at = datetime('now'), reviewed_by = ?
                 WHERE id = ? AND status = 'full'"
            )->execute([(int)$maint['id'], $id]);
            $flash = "Demoted editor #$id to minimal.";
            break;

        case 'reset_pending':
            $db->prepare(
                "UPDATE editor
                 SET status = 'pending', reviewed_at = datetime('now'), reviewed_by = ?
                 WHERE id = ? AND status IN ('minimal', 'full')"
            )->execute([(int)$maint['id'], $id]);
            $flash = "Reset editor #$id to pending.";
            break;

        case 'ban':
            $db->prepare(
                "UPDATE editor
                 SET status = 'banned', reviewed_at = datetime('now'), reviewed_by = ?
                 WHERE id = ? AND status != 'maintainer'"
            )->execute([(int)$maint['id'], $id]);
            $db->prepare(
                "UPDATE editor_session SET revoked_at = datetime('now')
                 WHERE editor_id = ? AND revoked_at IS NULL"
            )->execute([$id]);
            $flash = "Banned editor #$id and revoked their sessions.";
            break;

        case 'unban':
            $db->prepare(
                "UPDATE editor
                 SET status = 'pending', reviewed_at = datetime('now'), reviewed_by = ?
                 WHERE id = ? AND status = 'banned'"
            )->execute([(int)$maint['id'], $id]);
            $flash = "Unbanned editor #$id (reset to pending).";
            break;

        case 'revoke_sessions':
            $db->prepare(
                "UPDATE editor_session SET revoked_at = datetime('now')
                 WHERE editor_id = ? AND revoked_at IS NULL"
            )->execute([$id]);
            $flash = "Revoked all active sessions for editor #$id.";
            break;

        default:
            $flash_err = "Unknown action.";
    }
}

$status_filter = $_GET['status'] ?? 'all';
$statuses = ['pending', 'minimal', 'full', 'banned', 'maintainer', 'all'];
if (!in_array($status_filter, $statuses, true)) $status_filter = 'all';

$where = $status_filter === 'all' ? '' : 'WHERE status = ?';
$params = $status_filter === 'all' ? [] : [$status_filter];
$stmt = $db->prepare(
    "SELECT e.*,
            (SELECT COUNT(*) FROM change_request cr
             WHERE cr.editor_id = e.id AND cr.status = 'pending') AS n_pending,
            (SELECT COUNT(*) FROM change_request cr
             WHERE cr.editor_id = e.id AND cr.status = 'approved') AS n_approved,
            (SELECT COUNT(*) FROM editor_session s
             WHERE s.editor_id = e.id
               AND s.revoked_at IS NULL
               AND s.expires_at > datetime('now')) AS n_sessions
     FROM editor e
     $where
     ORDER BY CASE e.status
                WHEN 'pending' THEN 0
                WHEN 'minimal' THEN 1
                WHEN 'full'    THEN 2
                WHEN 'banned'  THEN 3
                WHEN 'maintainer' THEN 4
                ELSE 5 END,
              e.created_at DESC
     LIMIT 500"
);
$stmt->execute($params);
$editors = $stmt->fetchAll();

$csrf = htmlspecialchars(csrf_token());
header('Cache-Control: no-store');
include_header('Admin — editors');
?>
<h2>Editors</h2>

<?php if ($flash): ?>
<p style="padding:.5rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px"><?= htmlspecialchars($flash) ?></p>
<?php endif ?>
<?php if ($flash_err): ?>
<p style="padding:.5rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px"><?= htmlspecialchars($flash_err) ?></p>
<?php endif ?>

<p style="font-size:.85rem">
  Filter:
  <?php foreach ($statuses as $s):
      $cls = $s === $status_filter ? ' style="font-weight:700"' : ''; ?>
    <a href="/admin.php?status=<?= $s ?>"<?= $cls ?>><?= $s ?></a>&nbsp;
  <?php endforeach ?>
  <a href="/review.php" style="float:right">Review queue</a>
</p>

<?php // One tiny hidden form per editor row; per-row action buttons reference it via form="...". ?>
<?php foreach ($editors as $e): ?>
  <form id="ed-<?= (int)$e['id'] ?>" method="POST" action="/admin.php" style="display:none">
    <input type="hidden" name="csrf_token" value="<?= $csrf ?>">
    <input type="hidden" name="id" value="<?= (int)$e['id'] ?>">
  </form>
<?php endforeach ?>

<form method="POST" action="/admin.php">
  <input type="hidden" name="csrf_token" value="<?= $csrf ?>">
  <table class="desc-table" style="font-size:.85rem">
    <thead>
      <tr>
        <th></th>
        <th>Email</th><th>Name</th><th>Status</th>
        <th>Pending</th><th>Approved</th><th>Sessions</th>
        <th>Joined</th><th>Last login</th><th>Actions</th>
      </tr>
    </thead>
    <tbody>
      <?php foreach ($editors as $e):
        $eid = (int)$e['id'];
        $st  = (string)$e['status']; ?>
        <tr>
          <td>
            <?php if ($st === 'pending'): ?>
              <input type="checkbox" name="ids[]" value="<?= $eid ?>">
            <?php endif ?>
          </td>
          <td><?= htmlspecialchars((string)$e['email']) ?></td>
          <td><?= htmlspecialchars((string)($e['display_name'] ?? '')) ?></td>
          <td><?= htmlspecialchars($st) ?></td>
          <td><?= (int)$e['n_pending'] ?></td>
          <td><?= (int)$e['n_approved'] ?></td>
          <td><?= (int)$e['n_sessions'] ?></td>
          <td><?= htmlspecialchars((string)$e['created_at']) ?></td>
          <td><?= htmlspecialchars((string)($e['last_login_at'] ?? '')) ?></td>
          <td style="white-space:nowrap">
            <?php if ($st !== 'maintainer'): ?>
              <?php if ($st === 'pending'): ?>
                <button form="ed-<?= $eid ?>" name="action" value="approve_minimal">→minimal</button>
              <?php endif ?>
              <?php if (in_array($st, ['pending', 'minimal'], true)): ?>
                <button form="ed-<?= $eid ?>" name="action" value="promote">→full</button>
              <?php endif ?>
              <?php if ($st === 'full'): ?>
                <button form="ed-<?= $eid ?>" name="action" value="demote">→minimal</button>
              <?php endif ?>
              <?php if (in_array($st, ['minimal', 'full'], true)): ?>
                <button form="ed-<?= $eid ?>" name="action" value="reset_pending">→pending</button>
              <?php endif ?>
              <?php if ($st === 'banned'): ?>
                <button form="ed-<?= $eid ?>" name="action" value="unban">unban</button>
              <?php else: ?>
                <button form="ed-<?= $eid ?>" name="action" value="ban">ban</button>
              <?php endif ?>
              <?php if ((int)$e['n_sessions'] > 0): ?>
                <button form="ed-<?= $eid ?>" name="action" value="revoke_sessions">revoke sessions</button>
              <?php endif ?>
            <?php endif ?>
          </td>
        </tr>
      <?php endforeach ?>
    </tbody>
  </table>
  <p style="margin-top:.75rem">
    <button type="submit" name="action" value="bulk_approve">Approve selected (pending → minimal)</button>
  </p>
</form>

<?php include_footer(); ?>
