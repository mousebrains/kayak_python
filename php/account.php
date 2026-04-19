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
  <tr><td>Status</td><td><?= htmlspecialchars((string)$ed['status']) ?></td></tr>
  <tr><td>Joined</td><td><?= htmlspecialchars((string)$ed['created_at']) ?></td></tr>
  <?php if (!empty($ed['last_login_at'])): ?>
  <tr><td>Last login</td><td><?= htmlspecialchars((string)$ed['last_login_at']) ?></td></tr>
  <?php endif ?>
  <tr><td>Session expires</td><td><?= htmlspecialchars((string)$ed['session_expires_at']) ?></td></tr>
</table>

<p style="margin-top:1rem;font-size:.85rem;color:var(--c-text-muted)">
  The Comment link in the top nav lets you propose edits to reaches, gauges,
  and sources from their description pages. Your submissions are reviewed by
  the maintainer before being applied.
</p>

<p style="margin-top:1rem"><a href="/logout.php">Log out</a></p>

<?php include_footer(); ?>
