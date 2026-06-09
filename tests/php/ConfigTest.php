<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/FunctionalTestCase.php';

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
 * The end-to-end test resolves the `levels` CLI via KAYAK_LEVELS_BIN, then
 * FunctionalTestCase::resolveVenvCommand (prod venv -> .venv -> PATH), so it
 * runs in CI (which has `levels` on PATH); it skips (not fails) only when no
 * binary is found anywhere.
 */
final class ConfigTest extends TestCase
{
    public static function setUpBeforeClass(): void
    {
        require_once __DIR__ . '/../../src/kayak/web/php/includes/config.php';
    }

    protected function tearDown(): void
    {
        // Restore bootstrap's empty Config — null'ing the singleton
        // would die_500 the next test class's first Config read.
        Config::install_for_tests([]);
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

    public function testSiteReadsNestedBlock(): void
    {
        // S3a: site identity is a nested object emitted by `levels emit-config`.
        $this->_install_fixture(['site' => ['site_name' => 'Foo Levels', 'brand_color' => '#abcdef']]);
        $this->assertSame('Foo Levels', Config::site('site_name'));
        $this->assertSame('#abcdef', Config::site('brand_color'));
    }

    public function testSiteDefaultWhenBlockAbsent(): void
    {
        $this->_install_fixture([]);
        $this->assertSame('WKCC River Levels', Config::site('site_name', 'WKCC River Levels'));
    }

    public function testSiteDefaultWhenKeyMissingOrNonString(): void
    {
        $this->_install_fixture(['site' => ['brand_color' => 123]]);  // non-string value
        $this->assertSame('#1b5591', Config::site('brand_color', '#1b5591'));
        $this->assertSame('fallback', Config::site('absent_key', 'fallback'));
    }

    public function testReturnsDefaultWhenKeyAbsent(): void
    {
        // Phase 4 removed the getenv() fallback. A key not in the JSON
        // returns the wrapper's $default; env vars are irrelevant.
        $this->_install_fixture([]);
        putenv('SITE_URL=https://env-value-ignored.com/');
        try {
            $this->assertSame('https://fallback/', Config::str('site_url', 'https://fallback/'));
        } finally {
            putenv('SITE_URL');
        }
    }

    public function testEnvVarIgnoredWhenJsonPresent(): void
    {
        // Same key in env and JSON: JSON wins (only it gets read at all).
        $this->_install_fixture(['site_url' => 'https://from-json.com/']);
        putenv('SITE_URL=https://from-env-ignored.com/');
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
        // KAYAK_LEVELS_BIN override wins; otherwise reuse FunctionalTestCase's
        // resolver (prod venv -> .venv -> PATH) so CI, which puts `levels` on
        // PATH without setting the env, runs this instead of skipping (R4.1).
        $env = getenv('KAYAK_LEVELS_BIN');
        $envSet = is_string($env) && $env !== '';
        $bin = $envSet ? $env : FunctionalTestCase::resolveVenvCommand(dirname(__DIR__, 2));
        if ($bin === null || !is_executable($bin)) {
            if ($envSet) {
                $this->fail("KAYAK_LEVELS_BIN='$env' set but not executable");
            }
            $this->markTestSkipped('no `levels` CLI (KAYAK_LEVELS_BIN, prod venv, .venv, or PATH)');
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

    public function testFatalDie500OnMissingJson(): void
    {
        // Subprocess: Config::for_test on a non-existent path must log
        // [CONFIG-FATAL] and exit(1). We can't assert this in-process
        // because the SUT calls exit() which would kill PHPUnit.
        $script_dir = sys_get_temp_dir();
        $config_php = realpath(__DIR__ . '/../../src/kayak/web/php/includes/config.php');
        $this->assertNotFalse($config_php);
        $script = $script_dir . '/kayak-fatal-' . uniqid() . '.php';
        file_put_contents(
            $script,
            "<?php\nrequire_once " . var_export($config_php, true) . ";\n"
            . "Config::for_test('/nonexistent/path/runtime-config.json');\n"
            . "echo 'unreachable';\n",
        );
        $output_lines = [];
        $exit_code = 0;
        // ``error_log=`` directs PHP's error_log() at stderr so 2>&1
        // captures the [CONFIG-FATAL] line; ``log_errors=0`` keeps the
        // engine quiet on its own deprecations.
        exec(
            'php -d "error_log=" -d "log_errors=1" ' . escapeshellarg($script) . ' 2>&1',
            $output_lines,
            $exit_code,
        );
        @unlink($script);
        $combined = implode("\n", $output_lines);
        $this->assertSame(1, $exit_code, "exit code should be 1, got $exit_code; output: $combined");
        $this->assertStringContainsString('[CONFIG-FATAL]', $combined);
        $this->assertStringNotContainsString('unreachable', $combined);
    }

    public function testExtraKeysSilent(): void
    {
        // Phase 4.2 removed the ALWAYS_PRESENT_KEYS embedded schema —
        // the JSON IS the schema now. A JSON with extra keys not used
        // by PHP (ntfy_topic, hc_*, etc.) loads silently.
        $this->_install_fixture([
            'site_url' => 'https://example.com/',
            // The full pipeline JSON has many keys; ensure the loader
            // doesn't care which ones are PHP-relevant.
            'ntfy_topic' => 'kayak-test',
            'hc_pipeline' => 'https://hc-ping.com/abc',
        ]);

        $log = tempnam(sys_get_temp_dir(), 'kayak-extra-keys-');
        $this->assertNotFalse($log);
        $prev = ini_set('error_log', $log);
        try {
            $this->assertSame('https://example.com/', Config::str('site_url'));
            $contents = (string)file_get_contents($log);
            $this->assertSame('', $contents);
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
