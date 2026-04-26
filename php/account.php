<?php
declare(strict_types=1);
/**
 * Editor self-serve account page.
 *
 * Shows identity, status, session info, and lets the editor edit their own
 * display name. Phase 2+ adds: submission history, email change, session
 * revocation, account deletion.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();
$ed = require_editor();

$flash = null;
$flash_err = null;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();
    $action = $_POST['action'] ?? '';
    if ($action === 'set_display_name') {
        $name = trim((string)($_POST['display_name'] ?? ''));
        if (strlen($name) > 128) {
            $flash_err = "Display name too long (max 128 characters).";
        } else {
            $value = $name === '' ? null : $name;
            get_db()->prepare("UPDATE editor SET display_name = ? WHERE id = ?")
                    ->execute([$value, (int)$ed['id']]);
            $ed['display_name'] = $value;
            $flash = $value === null ? "Display name cleared." : "Display name saved.";
        }
    } else {
        $flash_err = "Unknown action.";
    }
}

$csrf = htmlspecialchars(csrf_token());
header('Cache-Control: no-store');
include_header('Account');
?>
<h2>Your account</h2>

<?php if ($flash): ?>
<p style="padding:.5rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px"><?= htmlspecialchars($flash) ?></p>
<?php endif ?>
<?php if ($flash_err): ?>
<p style="padding:.5rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px"><?= htmlspecialchars($flash_err) ?></p>
<?php endif ?>

<table class="desc-table">
  <tr><td>Email</td><td><?= htmlspecialchars((string)$ed['email']) ?></td></tr>
  <tr><td>Display name</td>
      <td>
        <form method="POST" action="/account.php" style="display:inline">
          <input type="hidden" name="csrf_token" value="<?= $csrf ?>">
          <input type="text" name="display_name"
                 value="<?= htmlspecialchars((string)($ed['display_name'] ?? '')) ?>"
                 maxlength="128" size="24">
          <button type="submit" name="action" value="set_display_name">save</button>
        </form>
        <span style="font-size:.85rem;color:var(--c-text-muted);margin-left:.5rem">
          Shown to maintainers in the review queue. Leave blank to clear.
        </span>
      </td>
  </tr>
  <tr><td>Status</td><td>
    <?= htmlspecialchars((string)$ed['status']) ?>
    <details style="display:inline-block;margin-left:.5rem;font-size:.85rem;color:var(--c-text-muted);vertical-align:middle">
      <summary style="cursor:pointer">what's this?</summary>
      <ul style="margin:.25rem 0 0 0;padding-left:1.1rem">
        <li><strong>pending</strong> — new account; waiting for the maintainer to approve.</li>
        <li><strong>minimal</strong> — can propose edits to a reach's description and features.</li>
        <li><strong>full</strong> — can also propose display name, put-in/take-out coordinates, classes, and flow range.</li>
        <li><strong>maintainer</strong> — direct edit access; reviews everyone else's proposals.</li>
      </ul>
    </details>
  </td></tr>
  <tr><td>Joined</td><td><?= htmlspecialchars((string)$ed['created_at']) ?></td></tr>
  <?php if (!empty($ed['last_login_at'])): ?>
  <tr><td>Last login</td><td><?= htmlspecialchars((string)$ed['last_login_at']) ?></td></tr>
  <?php endif ?>
  <tr><td>Session expires</td><td><?= htmlspecialchars((string)$ed['session_expires_at']) ?></td></tr>
</table>

<p style="margin-top:1rem;font-size:.85rem;color:var(--c-text-muted)">
  To suggest an edit to a specific reach, open that reach's description page
  and use the "Suggest an edit" button. For site-level feedback — new
  features, bugs, data sources to add — use the <a href="/comment.php">Comment</a>
  link in the footer. Your submissions are reviewed by the maintainer before
  being applied.
</p>

<p style="margin-top:1.5rem">
  <a href="/">&larr; Back to river levels</a>
  &nbsp;&middot;&nbsp;
  <a href="/logout.php">Log out</a>
</p>

<?php include_footer(); ?>
