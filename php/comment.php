<?php
declare(strict_types=1);
/**
 * Site-level feedback form — writes a change_request with
 * target_type='site', target_id=NULL.
 *
 * Requires a signed-in editor (magic-link or maintainer); anonymous
 * visitors are bounced to /login.php and return here.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/mail.php';
require_once __DIR__ . '/includes/sanity.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();
$ed = require_editor();
$db = get_db();

$errors = [];
$saved = false;

$DAILY_CAP = 5;
$count_stmt = $db->prepare(
    "SELECT COUNT(*) FROM change_request
     WHERE editor_id = ? AND target_type = 'site'
       AND submitted_at > datetime('now', '-1 day')"
);
$count_stmt->execute([$ed['id']]);
$submitted_today = (int)$count_stmt->fetchColumn();

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();

    if (($_POST['website'] ?? '') !== '') {
        // honeypot — silently discard
        $saved = true;
    } else {
        $subject = strip_html_tags(trim((string)($_POST['subject'] ?? '')));
        $body    = strip_html_tags(trim((string)($_POST['body']    ?? '')));
        $notes   = strip_html_tags(trim((string)($_POST['notes_to_maint'] ?? '')));

        if ($subject === '' && $body === '') {
            $errors[] = 'Please include a subject or a message.';
        }
        $issues = array_merge(
            check_text_length('subject', $subject, 256),
            check_text_length('body',    $body,    10000),
            check_text_length('notes_to_maint', $notes, 5000)
        );
        foreach (sanity_errors($issues) as $i) $errors[] = $i['message'];

        if ($submitted_today >= $DAILY_CAP) {
            $errors[] = "Daily site-comment cap of $DAILY_CAP reached.";
        }

        if (!$errors) {
            $payload = ['body' => $body];
            $db->prepare(
                "INSERT INTO change_request
                 (target_type, target_id, editor_id, submitted_at, subject,
                  payload_json, notes_to_maint, status)
                 VALUES ('site', NULL, ?, datetime('now'), ?, ?, ?, 'pending')"
            )->execute([
                $ed['id'],
                $subject !== '' ? $subject : '(site feedback)',
                json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE),
                $notes,
            ]);
            $cr_id = (int)$db->lastInsertId();

            $maint_emails = maintainer_emails();
            $site = rtrim(auth_env('SITE_URL') ?: 'https://levels.wkcc.org', '/');
            $email_body = render_maintainer_notification(
                'Site feedback',
                (string)$ed['email'],
                "Subject: $subject\n\n$body",
                $notes,
                "$site/review.php?id=$cr_id"
            );
            $email_subj = '[levels] site feedback: ' . substr($subject !== '' ? $subject : '(no subject)', 0, 60);
            foreach ($maint_emails as $to) {
                send_email($to, $email_subj, $email_body);
            }
            $saved = true;
        }
    }
}

$csrf = htmlspecialchars(csrf_token());
header('Cache-Control: no-store');
include_header('Site feedback');
?>
<h2>Site feedback</h2>
<p style="font-size:.85rem;color:var(--c-text-muted)">
  Use this form for thoughts about the site as a whole — layout, new features,
  data sources to add, bugs. For edits to a specific reach, use the
  <strong>Comment</strong> link on that reach's description page.
  <?= $submitted_today ?> of <?= $DAILY_CAP ?> daily site-comments used.
</p>

<?php if ($saved): ?>
  <p style="padding:.6rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px">
    Thanks — your feedback was recorded. The maintainer will review it.
  </p>
<?php else: ?>
  <?php if ($errors): ?>
    <ul style="padding:.6rem 1.4rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px">
      <?php foreach ($errors as $e): ?><li><?= htmlspecialchars($e) ?></li><?php endforeach ?>
    </ul>
  <?php endif ?>

  <form method="POST" action="/comment.php" class="edit-form">
    <input type="hidden" name="csrf_token" value="<?= $csrf ?>">
    <input type="text" name="website" value="" tabindex="-1" autocomplete="off"
           style="position:absolute;left:-9999px;width:1px;height:1px" aria-hidden="true">

    <label>Subject (optional)</label>
    <input type="text" name="subject" maxlength="256"
           value="<?= htmlspecialchars((string)($_POST['subject'] ?? '')) ?>">

    <label>Message</label>
    <textarea name="body" style="height:10em"><?= htmlspecialchars((string)($_POST['body'] ?? '')) ?></textarea>

    <label>Notes to maintainer (optional)</label>
    <textarea name="notes_to_maint" style="height:5em"><?= htmlspecialchars((string)($_POST['notes_to_maint'] ?? '')) ?></textarea>

    <button type="submit" style="margin-top:.75rem">Send feedback</button>
  </form>
<?php endif ?>

<?php include_footer(); ?>
