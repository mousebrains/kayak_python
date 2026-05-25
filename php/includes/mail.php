<?php
declare(strict_types=1);
/**
 * Outgoing mail helper.
 *
 * Uses PHP mail() which routes through /usr/sbin/sendmail; on prod that's
 * msmtp-mta configured to relay through Gmail (see hardening/msmtprc).
 *
 * For dev/test: set MAIL_DUMP_DIR to write messages to files instead of
 * sending them. Useful when working locally without MTA configured.
 *
 * MAIL_FROM / MAIL_REPLY_TO / MAIL_DUMP_DIR resolve via Config (JSON,
 * env fallback baked into Config::str). The hardcoded `noreply@<host>`
 * + `noreply@levels.wkcc.org` defaults remain for "neither JSON nor
 * env sets a value" cases.
 */

require_once __DIR__ . '/config.php';

function mail_from(): string {
    $v = Config::str('mail_from');
    if ($v !== '') return $v;
    $host = gethostname() ?: 'localhost';
    return "noreply@$host";
}

/**
 * Reply-To address. Distinct from From so DMARC/DKIM align with the From
 * domain (Gmail) while users see a polished reply address on the wkcc.org
 * domain. Override via env MAIL_REPLY_TO if needed.
 */
function mail_reply_to(): string {
    $v = Config::str('mail_reply_to');
    return $v !== '' ? $v : 'noreply@levels.wkcc.org';
}

function mail_dump_dir(): ?string {
    $v = Config::str('mail_dump_dir');
    return $v !== '' ? $v : null;
}

/**
 * Send a plain-text email.
 *
 * Returns true on apparent success. `mail()` reporting success only means
 * the message was handed to the MTA, not that it was delivered. Pass
 * $extra_headers like ['Reply-To' => 'someone@example.com'] to override
 * the defaults.
 *
 * @param array<string, string> $extra_headers
 */
function send_email(string $to, string $subject, string $body, array $extra_headers = []): bool {
    if (!filter_var($to, FILTER_VALIDATE_EMAIL)) {
        error_log("send_email: refused invalid recipient: $to");
        return false;
    }
    // Strip CR/LF from subject before it reaches mail()/syslog/file dump.
    // PHP's mail() does not reliably sanitize the subject across versions,
    // and callers may pass DB-sourced strings (reach names, contact
    // subjects) that we must not let escape into header context.
    // preg_replace returns null on regex error (impossible for this pattern)
    // or invalid UTF-8 in $subject — fall back to the original in either case.
    $subject = preg_replace('/[\r\n]+/', ' ', $subject) ?? $subject;
    $from = mail_from();
    $default_headers = [
        'From'                      => $from,
        'Reply-To'                  => mail_reply_to(),
        'MIME-Version'              => '1.0',
        'Content-Type'              => 'text/plain; charset=UTF-8',
        'Content-Transfer-Encoding' => '8bit',
        'X-Mailer'                  => 'kayak-levels',
    ];
    // Sanitize extra header values — strip CR/LF to prevent header injection.
    foreach ($extra_headers as $k => $v) {
        $default_headers[$k] = preg_replace('/[\r\n]+/', ' ', (string)$v);
    }
    $headers = implode("\r\n", array_map(
        fn($k, $v) => "$k: $v",
        array_keys($default_headers),
        array_values($default_headers)
    ));

    $dump = mail_dump_dir();
    if ($dump !== null) {
        if (!is_dir($dump)) @mkdir($dump, 0700, true);
        $stamp = date('YmdHis') . '-' . bin2hex(random_bytes(3));
        $safe_to = preg_replace('/[^\w.@+-]+/', '_', $to) ?? '_';
        $file = "$dump/mail-$stamp-$safe_to.txt";
        $out = "To: $to\n$headers\nSubject: $subject\n\n$body\n";
        file_put_contents($file, $out);
        openlog('kayak-mail', LOG_PID, LOG_MAIL);
        syslog(LOG_INFO, "dumped mail to $file (to=$to subj=" . substr($subject, 0, 80) . ")");
        closelog();
        return true;
    }

    $ok = mail($to, $subject, $body, $headers, "-f $from");
    openlog('kayak-mail', LOG_PID, LOG_MAIL);
    syslog($ok ? LOG_INFO : LOG_WARNING,
        ($ok ? 'sent' : 'FAILED') . " mail to=$to subj=" . substr($subject, 0, 80));
    closelog();
    return $ok;
}

/** Render the magic-link email body. */
function render_magic_link_email(string $link, string $ip, ?string $user_agent): string {
    $ua = $user_agent ? substr($user_agent, 0, 200) : '(unknown browser)';
    return <<<TXT
Hello,

Click the link below to sign in to WKCC River Levels. The link expires in
30 minutes and can only be used once.

  $link

This login was requested from:
  IP address: $ip
  Browser:    $ua

If you did not request a login, ignore this email; no account activity
will occur.

—
WKCC River Levels
https://levels.wkcc.org
TXT;
}

/** Render the maintainer notification when a change_request lands. */
function render_maintainer_notification(
    string $target_label,
    string $editor_email,
    string $summary,
    string $notes,
    string $review_url,
    string $source_url = ''
): string {
    $notes_block  = $notes === ''      ? '(none)'   : $notes;
    $source_block = $source_url === '' ? '(direct)' : $source_url;
    return <<<TXT
A change has been proposed.

Target:    $target_label
From:      $editor_email
Page:      $source_block
Review:    $review_url

Summary
-------
$summary

Notes to maintainer
-------------------
$notes_block

—
WKCC River Levels
TXT;
}

/** Render the decision email sent back to the editor. */
function render_editor_decision_email(
    string $target_label,
    string $decision,
    string $reviewer_note
): string {
    $note_block = $reviewer_note === '' ? '' : "\nNote from the maintainer:\n$reviewer_note\n";
    return <<<TXT
Your proposed change to $target_label has been $decision.
$note_block
Thank you for contributing.

—
WKCC River Levels
https://levels.wkcc.org
TXT;
}

/**
 * Render an in-progress reply from the maintainer — the proposal is still
 * pending but the maintainer has a question or comment to relay.
 */
function render_editor_reply_email(string $target_label, string $reply_body): string {
    return <<<TXT
The maintainer replied on your proposed change to $target_label:

$reply_body

Your proposal is still pending. You can update it by visiting the reach
and submitting again, or wait for the maintainer's next action.

—
WKCC River Levels
https://levels.wkcc.org
TXT;
}

/** Reply + close combined: one email covering both the reply and the closure. */
function render_editor_reply_and_close_email(string $target_label, string $reply_body): string {
    return <<<TXT
The maintainer replied on your proposed change to $target_label:

$reply_body

This proposal has been marked resolved. Thank you for contributing.

—
WKCC River Levels
https://levels.wkcc.org
TXT;
}
