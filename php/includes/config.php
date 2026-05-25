<?php

declare(strict_types=1);

/**
 * Typed runtime-config reader.
 *
 * Reads /etc/kayak/runtime-config.json (or $KAYAK_CONFIG_PATH for
 * test isolation), the JSON snapshot written by `levels emit-config`.
 * Static methods are the production API; instance methods exist for
 * test factories.
 *
 * Phase 4 of `docs/done/PLAN_tier3_closeout.md` § T3.3: the JSON is the
 * single source of truth. A missing or unparseable file is fatal —
 * the request 500s and PHP-FPM logs `[CONFIG-FATAL]` for triage.
 * The getenv() fallback that Phase 2 carried as a dual-read shim is
 * gone; a key absent from the JSON returns the wrapper's $default.
 * Keys outside the schema are silent (forward-compat).
 *
 * PHPStan level 8: the typed wrappers (str/int/bool/list/url) are
 * the public API; `Config::get()` returns mixed for the rare callers
 * that need the raw value.
 */
final class Config
{
    public const DEFAULT_PATH = '/etc/kayak/runtime-config.json';

    private static ?Config $singleton = null;

    /** @var array<string, mixed> */
    private array $data;

    /**
     * @param array<string, mixed> $data
     */
    private function __construct(array $data)
    {
        $this->data = $data;
    }

    /**
     * Production singleton, loaded once per request lifecycle.
     */
    public static function get_singleton(): self
    {
        if (self::$singleton === null) {
            $path = getenv('KAYAK_CONFIG_PATH') ?: '';
            if ($path === '') {
                $path = self::DEFAULT_PATH;
            }
            self::$singleton = self::load($path);
        }
        return self::$singleton;
    }

    /**
     * Test factory: install a config instance loaded from $path as the
     * active singleton. Callers tear down via `Config::reset_for_test()`.
     */
    public static function for_test(string $path): self
    {
        $instance = self::load($path);
        self::$singleton = $instance;
        return $instance;
    }

    /**
     * Test factory: install a config instance from an in-memory array,
     * bypassing the file load. Used by tests/php/bootstrap.php to seed
     * an empty Config so the singleton never falls through to die_500
     * trying to read /etc/kayak/runtime-config.json on the test runner.
     *
     * @param array<string, mixed> $data
     */
    public static function install_for_tests(array $data): self
    {
        $instance = new self($data);
        self::$singleton = $instance;
        return $instance;
    }

    /**
     * Clear the singleton so the next read re-loads (and possibly
     * die_500s if no JSON is present). Pair with bootstrap's
     * install_for_tests([]) in tearDown if a test mutated the singleton.
     */
    public static function reset_for_test(): void
    {
        self::$singleton = null;
    }

    private static function load(string $path): self
    {
        $raw = @file_get_contents($path);
        if ($raw === false) {
            self::die_500("runtime-config.json not readable: $path");
        }
        $parsed = json_decode($raw, true);
        if (!is_array($parsed)) {
            self::die_500("runtime-config.json parse failed: $path");
        }
        return new self($parsed);
    }

    /**
     * Log a CONFIG-FATAL line, return HTTP 500, and exit. Called from
     * load() when the JSON snapshot is missing or unparseable — the
     * request can't safely continue without resolved config.
     */
    private static function die_500(string $reason): never
    {
        error_log("[CONFIG-FATAL] $reason");
        if (!headers_sent()) {
            http_response_code(500);
            header('Content-Type: text/plain; charset=utf-8');
        }
        echo "Server configuration error. Check journalctl -u php-fpm for [CONFIG-FATAL].\n";
        exit(1);
    }

    /**
     * Return the raw JSON value for $key, or $default. Phase 4 removed
     * the getenv() shim; keys absent from the JSON get the wrapper's
     * declared default.
     *
     * @param mixed $default
     * @return mixed
     */
    public static function get(string $key, $default = null)
    {
        $cfg = self::get_singleton();
        return array_key_exists($key, $cfg->data) ? $cfg->data[$key] : $default;
    }

    public static function str(string $key, string $default = ''): string
    {
        $v = self::get($key, null);
        if (is_string($v)) {
            return $v;
        }
        if (is_int($v) || is_float($v) || is_bool($v)) {
            return (string)$v;
        }
        return $default;
    }

    public static function int(string $key, int $default = 0): int
    {
        $v = self::get($key, null);
        if (is_int($v)) {
            return $v;
        }
        if (is_string($v) && preg_match('/^-?\d+$/', $v) === 1) {
            return (int)$v;
        }
        return $default;
    }

    public static function bool(string $key, bool $default = false): bool
    {
        $v = self::get($key, null);
        if (is_bool($v)) {
            return $v;
        }
        if (is_int($v)) {
            return $v !== 0;
        }
        if (is_string($v)) {
            return $v === '1' || strcasecmp($v, 'true') === 0;
        }
        return $default;
    }

    /**
     * @param list<string> $default
     * @return list<string>
     */
    public static function list(string $key, array $default = []): array
    {
        $v = self::get($key, null);
        if (is_array($v)) {
            $out = [];
            foreach ($v as $item) {
                if (is_string($item) || is_int($item) || is_float($item)) {
                    $out[] = (string)$item;
                }
            }
            return $out;
        }
        if (is_string($v) && $v !== '') {
            $parts = array_map('trim', explode(',', $v));
            $parts = array_filter($parts, static fn (string $p): bool => $p !== '');
            return array_values($parts);
        }
        return $default;
    }

    public static function url(string $key, string $default = ''): string
    {
        $v = self::get($key, null);
        if (is_string($v) && filter_var($v, FILTER_VALIDATE_URL) !== false) {
            return $v;
        }
        return $default;
    }

    /**
     * Print the resolved config to stdout for `php show-config.php`
     * incident-response use. Matches `levels show-config --format table`
     * for human inspection.
     */
    public static function dump(): void
    {
        $cfg = self::get_singleton();
        if ($cfg->data === []) {
            echo "(no config data — JSON path missing or empty)\n";
            return;
        }
        $width = max(array_map('strlen', array_keys($cfg->data)));
        ksort($cfg->data);
        foreach ($cfg->data as $key => $value) {
            $rendered = match (true) {
                is_array($value) => $value === [] ? '(empty list)' : implode(', ', array_map('strval', $value)),
                is_bool($value)  => $value ? 'true' : 'false',
                is_null($value)  => '(null)',
                default          => (string)$value,
            };
            printf("  %-{$width}s  %s\n", $key, $rendered);
        }
    }
}
