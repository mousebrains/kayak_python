<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Base class for HTTP-level integration tests against the PHP layer.
 *
 * Each subclass shares a single `php -S` test server (started in
 * `setUpBeforeClass`, stopped in `tearDownAfterClass`) listening on a
 * randomly-assigned port (avoids port-reuse races on CI re-runs).
 *
 * Schema is seeded by `levels init-db` against a tmp SQLite file (option
 * (b) from docs/PLAN_php_layer_split.md Phase 1.3 — stays in lockstep
 * with src/kayak/db/models.py without a parallel SQL fixture).
 *
 * Env vars that PHP-FPM normally injects via nginx fastcgi_param are
 * passed to the test server in proc_open's env array. Endpoints that
 * need real network values (a real Turnstile site key, for example)
 * should be tested with mock/skip logic at the test-method level.
 *
 * Skipped (not failed) when the Python venv isn't available — local
 * PHP-only contributors aren't blocked from running unit tests.
 */
abstract class IntegrationTestCase extends TestCase
{
    /** @var resource|null */
    private static $serverProcess = null;
    /** @var array<int, resource> */
    private static array $serverPipes = [];
    private static int $serverPort = 0;
    private static string $dbPath = '';

    public static function setUpBeforeClass(): void
    {
        $repoRoot = dirname(__DIR__, 2);
        $venvLevels = self::resolveVenvCommand($repoRoot);
        if ($venvLevels === null) {
            self::markTestSkipped(
                "No `levels` CLI found (looked for venv in /home/pat/.venv, "
                . "$repoRoot/.venv, and PATH). Integration tests need it to "
                . "seed the test DB schema via `levels init-db`."
            );
            return;
        }

        // 1. Seed schema in a fresh tmp SQLite file.
        $dbPath = tempnam(sys_get_temp_dir(), 'kayak_test_') . '.db';
        // tempnam created an empty file at the name without .db; rename so
        // SQLite can open it cleanly (DATABASE_URL points at the .db path).
        if (file_exists($dbPath)) {
            unlink($dbPath);
        }
        $rc = 0;
        $output = [];
        $cmd = sprintf(
            'DATABASE_URL=%s %s init-db 2>&1',
            escapeshellarg('sqlite:///' . $dbPath),
            escapeshellarg($venvLevels),
        );
        exec($cmd, $output, $rc);
        if ($rc !== 0 || !file_exists($dbPath)) {
            throw new RuntimeException(
                "`levels init-db` failed (rc=$rc): " . implode("\n", $output)
            );
        }
        self::$dbPath = $dbPath;

        // Allow subclasses to seed additional rows on top of init-db's
        // reference data (states, fetch_urls, sources). Connects via a
        // throwaway PDO; the test server connects independently.
        $seedPdo = new PDO('sqlite:' . $dbPath);
        $seedPdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $seedPdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
        $seedPdo->exec('PRAGMA foreign_keys=ON');
        static::seedDatabase($seedPdo);
        $seedPdo = null;

        // 2. Spawn `php -S 127.0.0.1:0 -t <docroot>` with the env vars PHP
        // normally gets from nginx fastcgi_param plus the test SQLITE_PATH.
        $docroot = $repoRoot . '/public_html';
        $env = [
            'PATH' => getenv('PATH') ?: '/usr/bin:/bin',
            'HOME' => getenv('HOME') ?: '/tmp',
            'SQLITE_PATH' => $dbPath,
            'EDITOR_FEATURE' => '1',
            'MAIL_FROM' => 'test@example.com',
            'SITE_URL' => 'http://127.0.0.1',
            'TURNSTILE_SITE_KEY' => 'TEST_SITE_KEY',
            'TURNSTILE_SECRET' => 'TEST_SECRET',
        ];
        $descriptorspec = [
            0 => ['pipe', 'r'],  // stdin
            1 => ['pipe', 'w'],  // stdout
            2 => ['pipe', 'w'],  // stderr — `php -S` writes its "listening on" line here
        ];
        $cmd = ['php', '-S', '127.0.0.1:0', '-t', $docroot];
        $proc = proc_open($cmd, $descriptorspec, $pipes, $repoRoot, $env);
        if (!is_resource($proc)) {
            throw new RuntimeException('Failed to spawn php -S test server');
        }
        self::$serverProcess = $proc;
        self::$serverPipes = $pipes;
        stream_set_blocking($pipes[2], false);

        // 3. Parse the bound port from stderr. php -S 8.4 emits:
        //   [Mon May 12 ...] PHP 8.4.x Development Server (http://127.0.0.1:PORT) started
        $port = self::waitForPort($pipes[2], 5.0);
        if ($port === 0) {
            self::tearDownAfterClass();
            throw new RuntimeException('Test server never reported its bound port');
        }
        self::$serverPort = $port;
    }

    public static function tearDownAfterClass(): void
    {
        if (self::$serverProcess !== null && is_resource(self::$serverProcess)) {
            proc_terminate(self::$serverProcess);
            // Drain pipes so the child can exit cleanly.
            foreach (self::$serverPipes as $pipe) {
                if (is_resource($pipe)) {
                    fclose($pipe);
                }
            }
            proc_close(self::$serverProcess);
        }
        self::$serverProcess = null;
        self::$serverPipes = [];
        self::$serverPort = 0;
        if (self::$dbPath !== '' && file_exists(self::$dbPath)) {
            unlink(self::$dbPath);
        }
        self::$dbPath = '';
    }

    /**
     * Issue a request to the test server.
     *
     * @param array<string, scalar> $query    URL query params.
     * @param array<string, string> $cookies  Cookie name → value.
     * @param array<string, scalar> $post     Form fields (URL-encoded).
     * @return array{status:int, headers:array<string,string>, body:string}
     */
    protected function request(
        string $path,
        array $query = [],
        array $cookies = [],
        string $method = 'GET',
        array $post = [],
    ): array {
        $url = 'http://127.0.0.1:' . self::$serverPort . $path;
        if (!empty($query)) {
            $url .= (str_contains($path, '?') ? '&' : '?') . http_build_query($query);
        }

        $ch = curl_init($url);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_HEADER, true);
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
        curl_setopt($ch, CURLOPT_TIMEOUT, 10);
        curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 2);
        // Don't follow redirects — the assertion is about the immediate response.
        curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
        if (!empty($cookies)) {
            $cookieStr = implode('; ', array_map(
                fn($k, $v) => $k . '=' . $v,
                array_keys($cookies),
                array_values($cookies),
            ));
            curl_setopt($ch, CURLOPT_COOKIE, $cookieStr);
        }
        if ($method === 'POST' && !empty($post)) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($post));
        }

        $raw = curl_exec($ch);
        if ($raw === false) {
            // curl_close() is a no-op since PHP 8.0 and deprecated in 8.5;
            // omit it — the resource is freed when $ch goes out of scope.
            throw new RuntimeException("curl failed for $url: " . curl_error($ch));
        }
        $status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $headerSize = (int)curl_getinfo($ch, CURLINFO_HEADER_SIZE);

        $rawString = (string)$raw;
        $headerBlob = substr($rawString, 0, $headerSize);
        $body = substr($rawString, $headerSize);
        $headers = self::parseHeaders($headerBlob);

        return ['status' => $status, 'headers' => $headers, 'body' => $body];
    }

    /**
     * Assert that the body contains every needle (substring, in any order).
     *
     * Substring matching tolerates HTML attribute-order differences across
     * PHP versions; assoc-array iteration order isn't guaranteed stable so
     * golden-tests on the full body would be flaky.
     */
    protected function assertResponseContains(string $body, string ...$needles): void
    {
        foreach ($needles as $needle) {
            $this->assertStringContainsString(
                $needle,
                $body,
                'response body missing expected substring',
            );
        }
    }

    /**
     * Assert the body has no bare `<script>` tag — i.e. nothing the
     * nginx-side strict CSP would block. Substring match on the literal
     * `<script>` catches `<script>inline</script>` but not
     * `<script src=...>` (the latter has the attribute value before
     * the closing `>`).
     *
     * Carried forward from Phase 1.4's drill test (commit `d3e7dce`)
     * so the regression guard scales to all HTML-rendering tests.
     */
    protected function assertNoBareInlineScript(string $body): void
    {
        $this->assertStringNotContainsString(
            '<script>',
            $body,
            'inline <script> would clash with prod CSP',
        );
    }

    /**
     * Subclass hook to seed rows after `levels init-db` runs.
     *
     * Override in subclasses to insert reach/gauge/observation/etc. test
     * data via the provided PDO. Default is a no-op so subclasses that
     * only need the schema-plus-reference-data baseline don't need to
     * override anything. Runs once per test class (in setUpBeforeClass).
     */
    protected static function seedDatabase(PDO $db): void
    {
        // no-op by default
    }

    /**
     * Open a fresh PDO connection to the test DB. For per-test setup
     * outside the seedDatabase() baseline — e.g. seeding an editor
     * session row before a request that needs auth.
     */
    protected static function testDb(): PDO
    {
        $pdo = new PDO('sqlite:' . self::$dbPath);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
        $pdo->exec('PRAGMA foreign_keys=ON');
        return $pdo;
    }

    /**
     * Seed an editor row + a 7-day editor_session row. Returns the raw
     * session token (sha256 of which is stored server-side) plus a
     * separately-generated CSRF token. Pass them through `request()`'s
     * `$cookies` arg as `ed_sess` + `ed_csrf`, and for POSTs also
     * include `csrf_token` in `$post` so `require_csrf()` matches.
     *
     * Matches the cookie format checked by `current_editor()` (64-char
     * hex) and `require_csrf()` (double-submit). The CSRF cookie has
     * no DB row — it's pure double-submit, the cookie itself is
     * authoritative.
     *
     * @param  string $status  'pending'|'minimal'|'full'|'maintainer'|'banned'
     * @return array{editor_id: int, session_token: string, csrf_token: string}
     */
    protected static function seedEditorSession(string $email, string $status = 'full'): array
    {
        $db = self::testDb();
        $db->prepare(
            "INSERT INTO editor (email, status, created_at) VALUES (?, ?, datetime('now'))"
        )->execute([$email, $status]);
        $editor_id = (int)$db->lastInsertId();

        $session_token = bin2hex(random_bytes(32));
        $hash = hash('sha256', $session_token);
        $db->prepare(
            "INSERT INTO editor_session
                (editor_id, token_hash, expires_at, last_seen_at)
             VALUES (?, ?, datetime('now', '+7 days'), datetime('now'))"
        )->execute([$editor_id, $hash]);

        $csrf_token = bin2hex(random_bytes(32));

        return [
            'editor_id' => $editor_id,
            'session_token' => $session_token,
            'csrf_token' => $csrf_token,
        ];
    }

    /** Locate the `levels` CLI. Prefers the prod venv, then a local .venv, then PATH. */
    private static function resolveVenvCommand(string $repoRoot): ?string
    {
        $candidates = [
            '/home/pat/.venv/bin/levels',
            $repoRoot . '/.venv/bin/levels',
        ];
        foreach ($candidates as $candidate) {
            if (is_file($candidate) && is_executable($candidate)) {
                return $candidate;
            }
        }
        // Fall back to PATH lookup.
        $whichOutput = [];
        exec('which levels 2>/dev/null', $whichOutput, $rc);
        if ($rc === 0 && !empty($whichOutput)) {
            return trim($whichOutput[0]);
        }
        return null;
    }

    /** Read non-blocking stderr until the server announces its port (or timeout). */
    private static function waitForPort($stderrPipe, float $timeoutSeconds): int
    {
        $deadline = microtime(true) + $timeoutSeconds;
        $buf = '';
        while (microtime(true) < $deadline) {
            $chunk = fread($stderrPipe, 4096);
            if ($chunk !== false && $chunk !== '') {
                $buf .= $chunk;
                if (preg_match('#127\.0\.0\.1:(\d+)#', $buf, $m)) {
                    return (int)$m[1];
                }
            }
            usleep(50_000);  // 50ms
        }
        return 0;
    }

    /** @return array<string, string> */
    private static function parseHeaders(string $blob): array
    {
        $headers = [];
        foreach (explode("\r\n", $blob) as $line) {
            $line = trim($line);
            if ($line === '' || str_starts_with($line, 'HTTP/')) {
                continue;
            }
            $colon = strpos($line, ':');
            if ($colon === false) {
                continue;
            }
            $name = strtolower(trim(substr($line, 0, $colon)));
            $value = trim(substr($line, $colon + 1));
            $headers[$name] = $value;
        }
        return $headers;
    }
}
