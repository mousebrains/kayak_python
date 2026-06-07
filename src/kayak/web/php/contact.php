<?php
declare(strict_types=1);
/**
 * Anonymous contact form — emails the maintainer.
 *
 * GET  /contact.php      Show form (Turnstile + subject + body + reply-to).
 * POST /contact.php      Validate + send email.
 *
 * No account required. Turnstile is the only bot deterrent; if it isn't
 * configured the page still works (captcha bypassed, see turnstile.php).
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/mail.php';
require_once __DIR__ . '/includes/turnstile.php';
require_once __DIR__ . '/includes/sanity.php';
require_once __DIR__ . '/includes/source_url.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

// Intentionally not gated on require_editor_feature(): the contact form is
// for anyone (no account required), and the footer links to it
// unconditionally. Gating it would 404 the footer link when the editor
// feature is off.

$errors = [];
$saved  = false;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();

    // Honeypot — silently discard, claim success to the bot.
    if (($_POST['website'] ?? '') !== '') {
        $saved = true;
    } else {
        $from_addr = trim((string)($_POST['from_email'] ?? ''));
        $subject   = strip_html_tags(trim((string)($_POST['subject'] ?? '')));
        $body      = strip_html_tags(trim((string)($_POST['body']    ?? '')));

        if ($from_addr !== '' && filter_var($from_addr, FILTER_VALIDATE_EMAIL) === false) {
            $errors[] = 'Please enter a valid email address, or leave the field blank.';
        }
        if ($body === '') {
            $errors[] = 'Please include a message.';
        }
        $issues = array_merge(
            check_text_length('subject', $subject, 256),
            check_text_length('body',    $body,    10000)
        );
        foreach (sanity_errors($issues) as $i) $errors[] = $i['message'];

        if (!turnstile_verify(
            (string)($_POST['cf-turnstile-response'] ?? ''),
            (string)($_SERVER['REMOTE_ADDR'] ?? '')
        )) {
            $errors[] = 'Captcha verification failed. Please try again.';
        }

        if ($errors === []) {
            $ip       = (string)($_SERVER['REMOTE_ADDR'] ?? '');
            $ua       = substr((string)($_SERVER['HTTP_USER_AGENT'] ?? ''), 0, 200);
            $from_txt = $from_addr !== '' ? $from_addr : '(not provided)';
            $subj_txt = $subject   !== '' ? $subject   : '(no subject)';
            $src      = sanitize_source_url((string)($_POST['source_url'] ?? ''));
            $src_txt  = $src !== '' ? $src : '(direct)';
            $email_body = <<<TXT
Contact form submission from levels.wkcc.org

From:    $from_txt
Subject: $subj_txt
Page:    $src_txt
IP:      $ip
Browser: $ua

---
$body
---

Reply by hitting "Reply" on this email (works only if the visitor
supplied their address above — otherwise it bounces).
TXT;

            // Route to all maintainer addresses; Reply-To threads replies back
            // to the visitor when they gave an email.
            $extra = $from_addr !== ''
                ? ['Reply-To' => $from_addr]
                : [];
            foreach (maintainer_emails() as $to) {
                send_email(
                    $to,
                    "[levels] contact: " . substr($subj_txt, 0, 60),
                    $email_body,
                    $extra
                );
            }
            $saved = true;
        }
    }
}

$csrf = htmlspecialchars(csrf_token());
$source_url = $_SERVER['REQUEST_METHOD'] === 'POST'
    ? sanitize_source_url((string)($_POST['source_url'] ?? ''))
    : source_url_from_referrer('/contact.php');
header('Cache-Control: no-store');
include_header('Contact the maintainer', '', 'Send a message to the site maintainer.', turnstile_script_tag());
?>
<h2>Contact the maintainer</h2>
<p style="font-size:.85rem;color:var(--c-text-muted)">
  Send a message to the person who runs this site. No account needed.
  Leave your email if you want a reply.
</p>

<?php if ($saved): ?>
  <p style="padding:.6rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px">
    Thanks — your message is on its way.
  </p>
  <p style="margin-top:1rem"><a href="/">&larr; Back to river levels</a></p>
<?php else: ?>
  <?php if ($errors !== []): ?>
    <ul style="padding:.6rem 1.4rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px">
      <?php foreach ($errors as $e): ?><li><?= htmlspecialchars($e) ?></li><?php endforeach ?>
    </ul>
  <?php endif ?>

  <form method="POST" action="/contact.php" class="edit-form" style="max-width:640px">
    <input type="hidden" name="csrf_token" value="<?= $csrf ?>">
    <input type="hidden" name="source_url" value="<?= htmlspecialchars($source_url) ?>">
    <input type="text" name="website" value="" tabindex="-1" autocomplete="off"
           style="position:absolute;left:-9999px;width:1px;height:1px" aria-hidden="true">

    <label for="from_email">Your email (optional — so the maintainer can reply)</label>
    <input id="from_email" type="email" name="from_email" autocomplete="email"
           value="<?= htmlspecialchars((string)($_POST['from_email'] ?? '')) ?>">

    <label for="subject">Subject (optional)</label>
    <input id="subject" type="text" name="subject" maxlength="256"
           value="<?= htmlspecialchars((string)($_POST['subject'] ?? '')) ?>">

    <label for="body">Message</label>
    <textarea id="body" name="body" style="height:10em" required><?= htmlspecialchars((string)($_POST['body'] ?? '')) ?></textarea>

    <?= turnstile_widget() ?>

    <button type="submit" style="margin-top:.75rem">Send message</button>
  </form>
<?php endif ?>

<?php include_footer(); ?>
