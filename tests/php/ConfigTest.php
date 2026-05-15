<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

/**
 * Schema parity + behavior tests for php/includes/config.php.
 *
 * Two layers of coverage:
 *
 *   1. Unit tests against Config::for_test(jsonPath) with hand-crafted
 *      fixtures — typed wrapper behavior, fallback ordering, log-once
 *      semantics.
 *
 *   2. End-to-end test: spawn `levels emit-config --out=$tmp` against a
 *      known env, load the resulting JSON via Config::for_test, and
 *      assert every documented key resolves with the expected shape.
 *      This catches drift between KayakConfig (Python) and the typed
 *      wrappers (PHP) before runtime.
 *
 * Levels-binary path comes from `getenv('KAYAK_LEVELS_BIN')` with a
 * /home/pat/.venv default; CI sets the env var to the runner-specific
 * path. The test is skipped (not failed) when the binary is missing.
 */
final class ConfigTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../php/includes/config.php';
    }

    protected function tearDown(): void
    {
        Config::reset_for_test();
    }

    // ---------------------------------------------------------------
    // Typed wrapper unit tests
    // ---------------------------------------------------------------

    public function testStrFromJson(): void
    {
        $this->_install_fixture(['site_url' => 'https://example.com/']);
        $this->assertSame('https://example.com/', Config::str('site_url'));
    }

    public function testStrDefaultWhenKeyAbsent(): void
    {
        $this->_install_fixture([]);
        $this->assertSame('fallback', Config::str('nope', 'fallback'));
    }

    public function testIntFromJsonInt(): void
    {
        $this->_install_fixture(['fetch_timeout' => 300]);
        $this->assertSame(300, Config::int('fetch_timeout'));
    }

    public function testIntCoercesNumericString(): void
    {
        $this->_install_fixture(['fetch_timeout' => '300']);
        $this->assertSame(300, Config::int('fetch_timeout'));
    }

    public function testIntDefaultOnNonNumericString(): void
    {
        $this->_install_fixture(['fetch_timeout' => 'abc']);
        $this->assertSame(99, Config::int('fetch_timeout', 99));
    }

    public function testBoolFromJsonTrue(): void
    {
        $this->_install_fixture(['editor_feature' => true]);
        $this->assertTrue(Config::bool('editor_feature'));
    }

    public function testBoolFromJsonFalse(): void
    {
        $this->_install_fixture(['editor_feature' => false]);
        $this->assertFalse(Config::bool('editor_feature'));
    }

    public function testBoolFromStringOne(): void
    {
        $this->_install_fixture(['editor_feature' => '1']);
        $this->assertTrue(Config::bool('editor_feature'));
    }

    public function testListFromJsonArray(): void
    {
        $this->_install_fixture(['maintainer_emails' => ['a@x.com', 'b@y.com']]);
        $this->assertSame(['a@x.com', 'b@y.com'], Config::list('maintainer_emails'));
    }

    public function testListFromCsvString(): void
    {
        $this->_install_fixture(['maintainer_emails' => ' a@x.com , b@y.com ']);
        $this->assertSame(['a@x.com', 'b@y.com'], Config::list('maintainer_emails'));
    }

    public function testUrlAcceptsValidUrl(): void
    {
        $this->_install_fixture(['site_url' => 'https://example.com/']);
        $this->assertSame('https://example.com/', Config::url('site_url'));
    }

    public function testUrlDefaultOnInvalid(): void
    {
        $this->_install_fixture(['site_url' => 'not-a-url']);
        $this->assertSame('https://fallback/', Config::url('site_url', 'https://fallback/'));
    }

    public function testGetenvFallbackWhenJsonMissingKey(): void
    {
        $this->_install_fixture([]);
        putenv('SITE_URL=https://env-value.com/');
        try {
            $this->assertSame('https://env-value.com/', Config::str('site_url'));
        } finally {
            putenv('SITE_URL');
        }
    }

    public function testJsonWinsOverGetenv(): void
    {
        $this->_install_fixture(['site_url' => 'https://from-json.com/']);
        putenv('SITE_URL=https://from-env.com/');
        try {
            $this->assertSame('https://from-json.com/', Config::str('site_url'));
        } finally {
            putenv('SITE_URL');
        }
    }

    // ---------------------------------------------------------------
    // End-to-end schema parity vs `levels emit-config`
    // ---------------------------------------------------------------

    public function testEmitConfigJsonRoundTripsViaConfig(): void
    {
        $bin = (string)(getenv('KAYAK_LEVELS_BIN') ?: '/home/pat/.venv/bin/levels');
        if (!is_executable($bin)) {
            $this->markTestSkipped("levels binary not at $bin");
        }

        $tmp = tempnam(sys_get_temp_dir(), 'kayak-runtime-config-');
        $this->assertNotFalse($tmp);
        try {
            // Run emit-config against a controlled env so the JSON shape
            // is deterministic. Inherit PATH etc. via inherit-env=true.
            $env = $this->_controlled_env();
            $cmd = escapeshellarg($bin) . ' emit-config --out=' . escapeshellarg($tmp);
            $descriptor_spec = [1 => ['pipe', 'w'], 2 => ['pipe', 'w']];
            $proc = proc_open($cmd, $descriptor_spec, $pipes, null, $env);
            $this->assertIsResource($proc);
            $stdout = stream_get_contents($pipes[1]);
            $stderr = stream_get_contents($pipes[2]);
            fclose($pipes[1]);
            fclose($pipes[2]);
            $exit = proc_close($proc);
            $this->assertSame(0, $exit, "emit-config failed: $stderr / $stdout");

            Config::for_test($tmp);
            // Spot-check every typed wrapper against a known emit-config value.
            $this->assertSame('Test Maintainer', Config::str('maintainer_name'));
            $this->assertSame(123, Config::int('fetch_timeout'));
            $this->assertTrue(Config::bool('editor_feature'));
            $this->assertSame(['a@example.com', 'b@example.com'], Config::list('maintainer_emails'));
            $this->assertSame('https://emit-config-test.example.com', rtrim(Config::str('site_url'), '/'));
            // Derived key — proves emit-config's database_path step ran.
            $this->assertSame('/tmp/parity.db', Config::str('database_path'));
        } finally {
            @unlink($tmp);
        }
    }

    public function testSchemaCheckWarnsOnMissingExpectedKey(): void
    {
        // JSON with only a subset of ALWAYS_PRESENT_KEYS — Config should
        // log one [CONFIG-SCHEMA] WARN per missing key without failing.
        $tmp = tempnam(sys_get_temp_dir(), 'kayak-partial-');
        $this->assertNotFalse($tmp);
        file_put_contents($tmp, json_encode([
            'database_url' => 'sqlite:////tmp/test.db',
            // database_path, output_dir, fetch_*, maintainer_*, site_url,
            // editor_*, csp_log_path all missing.
        ]));

        $log = tempnam(sys_get_temp_dir(), 'kayak-schema-log-');
        $this->assertNotFalse($log);
        $prev = ini_set('error_log', $log);
        try {
            Config::for_test($tmp);
            $contents = (string)file_get_contents($log);
            $this->assertStringContainsString('[CONFIG-SCHEMA]', $contents);
            $this->assertStringContainsString("'site_url' missing", $contents);
            $this->assertStringContainsString("'csp_log_path' missing", $contents);
        } finally {
            if ($prev !== false) {
                ini_set('error_log', $prev);
            }
            @unlink($tmp);
            @unlink($log);
        }
    }

    public function testSchemaCheckSilentWhenAllKeysPresent(): void
    {
        // Hand-rolled full JSON should produce zero [CONFIG-SCHEMA] lines.
        $tmp = tempnam(sys_get_temp_dir(), 'kayak-full-');
        $this->assertNotFalse($tmp);
        file_put_contents($tmp, json_encode([
            'database_url'            => 'sqlite:////tmp/test.db',
            'database_path'           => '/tmp/test.db',
            'output_dir'              => '/tmp',
            'fetch_timeout'           => 300,
            'fetch_budget'            => 240,
            'fetch_user_agent'        => 'kayak/1.0',
            'maintainer_emails'       => [],
            'maintainer_name'         => 'Test',
            'site_url'                => 'https://test.example.com/',
            'editor_feature'          => true,
            'editor_session_ttl_days' => 7,
            'csp_log_path'            => '/tmp/csp.log',
        ]));

        $log = tempnam(sys_get_temp_dir(), 'kayak-no-schema-warn-');
        $this->assertNotFalse($log);
        $prev = ini_set('error_log', $log);
        try {
            Config::for_test($tmp);
            $contents = (string)file_get_contents($log);
            $this->assertStringNotContainsString('[CONFIG-SCHEMA]', $contents);
        } finally {
            if ($prev !== false) {
                ini_set('error_log', $prev);
            }
            @unlink($tmp);
            @unlink($log);
        }
    }

    public function testFallbackLogLineFiresWhenJsonAbsent(): void
    {
        // for_test() on a non-existent path triggers the [CONFIG-FALLBACK]
        // warn. Capture stderr via PHP's error_log → /dev/stderr to assert
        // the log line fired.
        $log = tempnam(sys_get_temp_dir(), 'kayak-error-log-');
        $this->assertNotFalse($log);
        $prev = ini_set('error_log', $log);
        try {
            Config::for_test('/nonexistent/path/runtime-config.json');
            // Force one read so the log fires (lazy load).
            Config::str('site_url');
            $contents = (string)file_get_contents($log);
            $this->assertStringContainsString('[CONFIG-FALLBACK]', $contents);
        } finally {
            if ($prev !== false) {
                ini_set('error_log', $prev);
            }
            @unlink($log);
        }
    }

    // ---------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------

    /**
     * @param array<string, mixed> $data
     */
    private function _install_fixture(array $data): void
    {
        $tmp = tempnam(sys_get_temp_dir(), 'kayak-config-fixture-');
        $this->assertNotFalse($tmp);
        // Register tmp for cleanup on tearDown via register_shutdown_function
        // would leak across tests; instead, unlink at next tearDown.
        register_shutdown_function(static function () use ($tmp): void {
            @unlink($tmp);
        });
        file_put_contents($tmp, json_encode($data));
        Config::for_test($tmp);
    }

    /**
     * @return array<string, string>
     */
    private function _controlled_env(): array
    {
        // Minimal env that drives emit-config to a deterministic output.
        // Inherit PATH so the levels binary can find its venv interpreter.
        return [
            'PATH'              => (string)(getenv('PATH') ?: '/usr/bin:/bin'),
            'HOME'              => (string)(getenv('HOME') ?: '/tmp'),
            'DATABASE_URL'      => 'sqlite:////tmp/parity.db',
            'OUTPUT_DIR'        => '/tmp',
            'FETCH_TIMEOUT'     => '123',
            'MAINTAINER_NAME'   => 'Test Maintainer',
            'MAINTAINER_EMAIL'  => 'a@example.com,b@example.com',
            'SITE_URL'          => 'https://emit-config-test.example.com',
            'EDITOR_FEATURE'    => 'true',
        ];
    }
}
