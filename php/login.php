<?php
declare(strict_types=1);
/**
 * Editor login — email + magic-link flow.
 *
 * GET  /login.php[?next=/path]            Show email form
 * POST /login.php                         Issue and send magic link
 *
 * Feature-flagged behind EDITOR_FEATURE. When off, returns 404.
 */
require_once __DIR__ . '/includes/db.php';
require_once __DIR__ . '/includes/auth.php';
require_once __DIR__ . '/includes/mail.php';
require_once __DIR__ . '/includes/hcaptcha.php';
require_once __DIR__ . '/includes/header.php';
require_once __DIR__ . '/includes/footer.php';

require_editor_feature();

$next = safe_next_url($_GET['next'] ?? $_POST['next'] ?? null);

// Already signed in? Go home (or to next).
$ed_current = current_editor();
if ($ed_current !== null) {
    header("Location: $next");
    exit;
}

$error = '';
$info  = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    require_csrf();

    $email = normalize_email((string)($_POST['email'] ?? ''));
    if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
        $error = 'Please enter a valid email address.';
    } elseif (!hcaptcha_verify((string)($_POST['h-captcha-response'] ?? ''),
                               (string)($_SERVER['REMOTE_ADDR'] ?? ''))) {
        $error = 'Captcha verification failed. Please try again.';
    } else {
        try {
            $issued = issue_magic_link($email, $next);
            if (!$issued['banned']) {
                $tok  = $issued['token'];
                $site_env = auth_env('SITE_URL');
                $base = rtrim($site_env !== '' ? $site_env : 'https://levels.wkcc.org', '/');
                $link = $base . '/auth.php?t=' . urlencode($tok)
                      . '&next=' . rawurlencode($next);
                $body = render_magic_link_email(
                    $link,
                    (string)($_SERVER['REMOTE_ADDR'] ?? ''),
                    $_SERVER['HTTP_USER_AGENT'] ?? null
                );
                send_email($email, 'Sign in to WKCC River Levels', $body);
            }
            // Same response whether the email existed, was new, or is banned.
            $info = 'If that email can sign in, a login link is on its way. '
                  . 'The link expires in 30 minutes.';
        } catch (Throwable $e) {
            error_log('login.php: ' . $e->getMessage());
            $error = 'Something went wrong sending the email. Please try again.';
        }
    }
}

$csrf = htmlspecialchars(csrf_token());
$next_attr = htmlspecialchars($next);

header('Cache-Control: no-store');
include_header('Sign in', '', 'Sign in to WKCC River Levels.', hcaptcha_script_tag());
?>
<h2>Sign in</h2>

<?php if ($info): ?>
<p style="padding:.6rem;background:#e8f4ea;border:1px solid #b7dcc0;border-radius:4px"><?= htmlspecialchars($info) ?></p>
<?php endif ?>

<?php if ($error): ?>
<p style="padding:.6rem;background:#fde8e8;border:1px solid #f5b5b5;border-radius:4px"><?= htmlspecialchars($error) ?></p>
<?php endif ?>

<?php if (!$info): ?>
<form method="POST" action="/login.php" class="edit-form" style="max-width:420px">
  <input type="hidden" name="csrf_token" value="<?= $csrf ?>">
  <input type="hidden" name="next" value="<?= $next_attr ?>">
  <label for="email">Email address</label>
  <input type="email" id="email" name="email" required autocomplete="email"
         value="<?= htmlspecialchars((string)($_POST['email'] ?? '')) ?>">
  <?= hcaptcha_widget() ?>
  <button type="submit" style="margin-top:.75rem">Email me a login link</button>
</form>

<p style="margin-top:1.5rem;font-size:.85rem;color:var(--c-text-muted)">
  We will email you a one-time link that signs you in for seven days. No
  password is required. New addresses are created as a <em>pending</em>
  account; the maintainer decides when to widen access.
</p>
<?php endif ?>

<?php include_footer(); ?>
