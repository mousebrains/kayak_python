<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../php/includes/mail.php';

/**
 * Unit tests for php/includes/mail.php.
 *
 * The Config-backed accessors and the pure render_* body builders are
 * plain units. send_email() is driven through the MAIL_DUMP_DIR seam so
 * the header-injection-stripping path runs without an MTA — the dumped
 * file is the observable output.
 */
final class MailTest extends TestCase
{
    private string $dumpDir = '';

    protected function tearDown(): void
    {
        // Restore the bootstrap's empty Config so the next class isn't poisoned.
        Config::install_for_tests([]);
        if ($this->dumpDir !== '' && is_dir($this->dumpDir)) {
            foreach (glob($this->dumpDir . '/*') ?: [] as $f) {
                @unlink($f);
            }
            @rmdir($this->dumpDir);
        }
        $this->dumpDir = '';
    }

    /** @param array<string, mixed> $cfg */
    private function installConfig(array $cfg): void
    {
        Config::install_for_tests($cfg);
    }

    private function useDumpDir(): string
    {
        $dir = sys_get_temp_dir() . '/kayak-mail-test-' . bin2hex(random_bytes(4));
        $this->dumpDir = $dir;
        return $dir;
    }

    // --- Config-backed accessors -----------------------------------------

    public function test_mail_from_default_has_noreply(): void
    {
        $this->installConfig([]);
        // Falls back to noreply@<hostname>; the local-part is fixed.
        $this->assertStringStartsWith('noreply@', mail_from());
    }

    public function test_mail_from_override(): void
    {
        $this->installConfig(['mail_from' => 'bot@levels.wkcc.org']);
        $this->assertSame('bot@levels.wkcc.org', mail_from());
    }

    public function test_mail_reply_to_default(): void
    {
        $this->installConfig([]);
        $this->assertSame('noreply@levels.wkcc.org', mail_reply_to());
    }

    public function test_mail_reply_to_override(): void
    {
        $this->installConfig(['mail_reply_to' => 'help@example.com']);
        $this->assertSame('help@example.com', mail_reply_to());
    }

    public function test_mail_dump_dir_null_when_unset(): void
    {
        $this->installConfig([]);
        $this->assertNull(mail_dump_dir());
    }

    public function test_mail_dump_dir_value(): void
    {
        $this->installConfig(['mail_dump_dir' => '/tmp/x']);
        $this->assertSame('/tmp/x', mail_dump_dir());
    }

    // --- send_email via dump dir -----------------------------------------

    public function test_send_email_refuses_invalid_recipient(): void
    {
        $this->installConfig(['mail_dump_dir' => $this->useDumpDir()]);
        $this->assertFalse(send_email('not-an-email', 'Subj', 'Body'));
        // Nothing should have been written.
        $this->assertSame([], glob($this->dumpDir . '/*') ?: []);
    }

    public function test_send_email_dumps_file_and_returns_true(): void
    {
        $dir = $this->useDumpDir();
        $this->installConfig(['mail_dump_dir' => $dir, 'mail_from' => 'bot@levels.wkcc.org']);
        $ok = send_email('user@example.com', 'Hello there', 'Body line');
        $this->assertTrue($ok);

        $files = glob($dir . '/*') ?: [];
        $this->assertCount(1, $files);
        $contents = (string) file_get_contents($files[0]);
        $this->assertStringContainsString('To: user@example.com', $contents);
        $this->assertStringContainsString('Subject: Hello there', $contents);
        $this->assertStringContainsString('From: bot@levels.wkcc.org', $contents);
        $this->assertStringContainsString("\nBody line\n", $contents);
    }

    public function test_send_email_strips_crlf_from_subject(): void
    {
        $dir = $this->useDumpDir();
        $this->installConfig(['mail_dump_dir' => $dir]);
        // A subject carrying a header-injection payload must be flattened to a
        // single line — the injected Bcc header must NOT appear on its own line.
        $ok = send_email('user@example.com', "Subj\r\nBcc: evil@x.com", 'Body');
        $this->assertTrue($ok);

        $files = glob($dir . '/*') ?: [];
        $contents = (string) file_get_contents($files[0]);
        // CR/LF collapsed to a space → the payload stays on the Subject line.
        $this->assertStringContainsString('Subject: Subj Bcc: evil@x.com', $contents);
        $this->assertStringNotContainsString("\nBcc: evil@x.com", $contents);
    }

    public function test_send_email_strips_crlf_from_extra_headers(): void
    {
        $dir = $this->useDumpDir();
        $this->installConfig(['mail_dump_dir' => $dir]);
        $ok = send_email(
            'user@example.com',
            'Subj',
            'Body',
            ['Reply-To' => "ok@example.com\r\nBcc: evil@x.com"]
        );
        $this->assertTrue($ok);

        $files = glob($dir . '/*') ?: [];
        $contents = (string) file_get_contents($files[0]);
        $this->assertStringContainsString('Reply-To: ok@example.com Bcc: evil@x.com', $contents);
        // The injected Bcc must not become a standalone header line.
        $this->assertSame(0, preg_match('/^Bcc:/m', $contents));
    }

    public function test_send_email_sanitizes_recipient_in_dump_filename(): void
    {
        $dir = $this->useDumpDir();
        $this->installConfig(['mail_dump_dir' => $dir]);
        send_email('a.b+tag@example.com', 'Subj', 'Body');
        $files = glob($dir . '/*') ?: [];
        $this->assertCount(1, $files);
        // Allowed chars survive; '@', '.', '+', '-' are kept by the filter.
        $this->assertStringContainsString('a.b+tag@example.com', basename($files[0]));
    }

    // --- render_* body builders ------------------------------------------

    public function test_render_magic_link_email_with_user_agent(): void
    {
        $body = render_magic_link_email('https://x/login?t=abc', '1.2.3.4', 'Mozilla/5.0');
        $this->assertStringContainsString('https://x/login?t=abc', $body);
        $this->assertStringContainsString('IP address: 1.2.3.4', $body);
        $this->assertStringContainsString('Mozilla/5.0', $body);
    }

    public function test_render_magic_link_email_unknown_browser(): void
    {
        // Null and empty user-agent both render the "(unknown browser)" fallback.
        $this->assertStringContainsString('(unknown browser)', render_magic_link_email('L', 'IP', null));
        $this->assertStringContainsString('(unknown browser)', render_magic_link_email('L', 'IP', ''));
    }

    public function test_render_maintainer_notification_with_and_without_optionals(): void
    {
        $full = render_maintainer_notification('Reach 7', 'ed@x.com', 'sum', 'my notes', 'https://r', 'https://src');
        $this->assertStringContainsString('Target:    Reach 7', $full);
        $this->assertStringContainsString('From:      ed@x.com', $full);
        $this->assertStringContainsString('Page:      https://src', $full);
        $this->assertStringContainsString('my notes', $full);

        // Empty notes + empty source_url use the bracketed placeholders.
        $bare = render_maintainer_notification('Reach 7', 'ed@x.com', 'sum', '', 'https://r');
        $this->assertStringContainsString('(none)', $bare);
        $this->assertStringContainsString('(direct)', $bare);
    }

    public function test_render_editor_decision_email_with_and_without_note(): void
    {
        $withNote = render_editor_decision_email('Reach 7', 'approved', 'Looks good');
        $this->assertStringContainsString('has been approved', $withNote);
        $this->assertStringContainsString('Note from the maintainer:', $withNote);
        $this->assertStringContainsString('Looks good', $withNote);

        $noNote = render_editor_decision_email('Reach 7', 'rejected', '');
        $this->assertStringContainsString('has been rejected', $noNote);
        $this->assertStringNotContainsString('Note from the maintainer:', $noNote);
    }

    public function test_render_editor_reply_email(): void
    {
        $body = render_editor_reply_email('Reach 7', 'Can you clarify the put-in?');
        $this->assertStringContainsString('Reach 7', $body);
        $this->assertStringContainsString('Can you clarify the put-in?', $body);
        $this->assertStringContainsString('still pending', $body);
    }

    public function test_render_editor_reply_and_close_email(): void
    {
        $body = render_editor_reply_and_close_email('Reach 7', 'Thanks, merged manually.');
        $this->assertStringContainsString('Reach 7', $body);
        $this->assertStringContainsString('Thanks, merged manually.', $body);
        $this->assertStringContainsString('resolved', $body);
    }
}
