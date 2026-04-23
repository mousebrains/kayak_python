<?php
declare(strict_types=1);
/**
 * Editor self-serve account page (Phase 1 skeleton).
 *
 * Shows identity, status, session info. Phase 2 adds: submission history,
 * display-name edit, email change, session revocation, account deletion.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();
$ed = require_editor();

header('Cache-Control: no-store');
include_header('Account');
?>
<h2>Your account</h2>

<table class="desc-table">
  <tr><td>Email</td><td><?= htmlspecialchars((string)$ed['email']) ?></td></tr>
  <?php if (!empty($ed['display_name'])): ?>
  <tr><td>Display name</td><td><?= htmlspecialchars((string)$ed['display_name']) ?></td></tr>
  <?php endif ?>
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
