<?php
declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Exercises the T2-9 rate-limit logic in magic_link_under_throttle().
 * Creates editor + magic_link rows in an in-memory SQLite DB directly,
 * then calls the helper.
 */
final class AuthTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        if (!function_exists('get_db')) {
            eval('function get_db(): PDO { return new PDO("sqlite::memory:"); }');
        }
        require_once __DIR__ . '/../../php/includes/auth.php';
    }

    private function seedEditor(PDO $db, string $email): int
    {
        $db->prepare("INSERT INTO editor (email, status) VALUES (?, 'pending')")
           ->execute([$email]);
        return (int)$db->lastInsertId();
    }

    private function seedMagicLink(PDO $db, int $editorId, string $ip, string $minutesAgo): void
    {
        $db->prepare(
            "INSERT INTO editor_magic_link
                (editor_id, token_hash, created_at, expires_at, ip_issued)
             VALUES (?, ?, datetime('now', ?), datetime('now', '+30 minutes'), ?)"
        )->execute([$editorId, bin2hex(random_bytes(32)), $minutesAgo, $ip]);
    }

    public function testUnderCapAllows(): void
    {
        $db = kayak_test_pdo();
        $editorId = $this->seedEditor($db, 'a@example.com');
        // 3 recent magic links for this email — still under the cap of 5.
        for ($i = 0; $i < 3; $i++) {
            $this->seedMagicLink($db, $editorId, '1.2.3.4', '-10 minutes');
        }
        $this->assertTrue(
            magic_link_under_throttle($db, 'a@example.com', '1.2.3.4')
        );
    }

    public function testOverEmailCapBlocks(): void
    {
        $db = kayak_test_pdo();
        $editorId = $this->seedEditor($db, 'a@example.com');
        // 5 links in the last hour — at the email cap.
        for ($i = 0; $i < 5; $i++) {
            $this->seedMagicLink($db, $editorId, '1.2.3.4', '-10 minutes');
        }
        $this->assertFalse(
            magic_link_under_throttle($db, 'a@example.com', '1.2.3.4'),
            'should block when email hits the per-hour cap'
        );
    }

    public function testOldLinksDoNotCount(): void
    {
        $db = kayak_test_pdo();
        $editorId = $this->seedEditor($db, 'a@example.com');
        // 10 links from 2 hours ago — outside the 1-hour window.
        for ($i = 0; $i < 10; $i++) {
            $this->seedMagicLink($db, $editorId, '1.2.3.4', '-2 hours');
        }
        $this->assertTrue(
            magic_link_under_throttle($db, 'a@example.com', '1.2.3.4'),
            'links older than 1h must not contribute to the throttle'
        );
    }

    public function testOverIpCapBlocks(): void
    {
        $db = kayak_test_pdo();
        // Spread across many distinct emails but all from the same IP.
        for ($i = 0; $i < 20; $i++) {
            $editorId = $this->seedEditor($db, "u{$i}@example.com");
            $this->seedMagicLink($db, $editorId, '4.3.2.1', '-5 minutes');
        }
        $this->assertFalse(
            magic_link_under_throttle($db, 'fresh@example.com', '4.3.2.1'),
            'shared-IP cap (20/hour) must kick in regardless of email'
        );
    }
}
