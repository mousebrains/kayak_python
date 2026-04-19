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
 */

function _mail_env(string $name): string {
    $v = getenv($name);
    if ($v === false || $v === '') $v = (string)($_SERVER[$name] ?? '');
    return $v;
}

function mail_from(): string {
    $from = _mail_env('MAIL_FROM');
    if ($from !== '') return $from;
    $host = gethostname() ?: 'localhost';
    return "noreply@$host";
}

function mail_dump_dir(): ?string {
    $dir = _mail_env('MAIL_DUMP_DIR');
    return $dir !== '' ? $dir : null;
}

/**
 * Send a plain-text email.
 *
 * Returns true on apparent success. `mail()` reporting success only means
 * the message was handed to the MTA, not that it was delivered.
 */
function send_email(string $to, string $subject, string $body): bool {
    if (!filter_var($to, FILTER_VALIDATE_EMAIL)) {
        error_log("send_email: refused invalid recipient: $to");
        return false;
    }
    $from = mail_from();
    $headers = implode("\r\n", [
        "From: $from",
        "Reply-To: $from",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "Content-Transfer-Encoding: 8bit",
        "X-Mailer: kayak-levels",
    ]);

    $dump = mail_dump_dir();
    if ($dump !== null) {
        if (!is_dir($dump)) @mkdir($dump, 0700, true);
        $stamp = date('YmdHis') . '-' . bin2hex(random_bytes(3));
        $safe_to = preg_replace('/[^\w.@+-]+/', '_', $to);
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
    string $review_url
): string {
    $notes_block = $notes === '' ? '(none)' : $notes;
    return <<<TXT
A change has been proposed.

Target:    $target_label
From:      $editor_email
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
