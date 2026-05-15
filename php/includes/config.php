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
 * Phase 2 of `docs/PLAN_tier3_closeout.md` § T3.3: when the JSON is
 * missing, parse-failed, or the requested key is absent, the reader
 * falls back to `getenv($KEY_UPPER)` and logs a single
 * `[CONFIG-FALLBACK]` line per request. The FPM-pool env channel
 * (`env[X] = $X` in deploy/kayak-fpm-pool.conf) still carries values
 * after Phase 2.5 drops the nginx fastcgi_param lines, so the
 * fallback stays load-bearing for keys whose JSON-emit isn't wired
 * yet.
 *
 * PHPStan level 8: the typed wrappers (str/int/bool/list/url) are
 * the public API; `Config::get()` returns mixed for the rare callers
 * that need the raw value.
 */
final class Config
{
    public const DEFAULT_PATH = '/etc/kayak/runtime-config.json';

    /**
     * Keys the JSON snapshot is contracted to carry on every emit.
     *
     * These are the fields KayakConfig declares with non-None defaults
     * (or non-null derived keys like database_path), so emit-config
     * always writes them out. A key listed here that's absent from
     * the loaded JSON triggers a schema-drift WARN at load time — the
     * usual cause is a stale runtime-config.json that pre-dates a
     * newly-added KayakConfig field.
     *
     * Optional fields (mail_from, turnstile_*, ntfy_topic, hc_* —
     * any field declared `T | None` with default None) are
     * intentionally NOT listed; their absence is legitimate.
     */
    private const ALWAYS_PRESENT_KEYS = [
        'database_url',
        'database_path',
        'output_dir',
        'fetch_timeout',
        'fetch_budget',
        'fetch_user_agent',
        'maintainer_emails',
        'maintainer_name',
        'site_url',
        'editor_feature',
        'editor_session_ttl_days',
        'csp_log_path',
    ];

    private static ?Config $singleton = null;

    /** @var bool Static so the warn line fires at most once per request. */
    private static bool $fallback_logged = false;

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
            $path = (string)(getenv('KAYAK_CONFIG_PATH') ?: '');
            if ($path === '') {
                $path = self::DEFAULT_PATH;
            }
            self::$singleton = self::load($path);
        }
        return self::$singleton;
    }

    /**
     * Test factory: install a config instance loaded from $path as the
     * active singleton. Callers tear down via `Config::reset_for_test()`
     * to restore default behavior.
     */
    public static function for_test(string $path): self
    {
        $instance = self::load($path);
        self::$singleton = $instance;
        self::$fallback_logged = false;
        return $instance;
    }

    /**
     * Clear the singleton + fallback flag so the next read re-loads.
     * Called from test teardown.
     */
    public static function reset_for_test(): void
    {
        self::$singleton = null;
        self::$fallback_logged = false;
    }

    private static function load(string $path): self
    {
        $raw = @file_get_contents($path);
        if ($raw === false) {
            self::log_fallback_once("runtime-config.json not readable: $path");
            return new self([]);
        }
        $parsed = json_decode($raw, true);
        if (!is_array($parsed)) {
            self::log_fallback_once("runtime-config.json parse failed: $path");
            return new self([]);
        }
        self::check_schema($parsed, $path);
        return new self($parsed);
    }

    /**
     * Compare $data's keys against the ALWAYS_PRESENT_KEYS contract;
     * log one WARN per missing key. Missing keys still resolve via
     * the getenv fallback in Config::get(); this just makes the
     * schema-drift visible in php-fpm logs.
     *
     * @param array<string, mixed> $data
     */
    private static function check_schema(array $data, string $path): void
    {
        $missing = [];
        foreach (self::ALWAYS_PRESENT_KEYS as $key) {
            if (!array_key_exists($key, $data)) {
                $missing[] = $key;
            }
        }
        foreach ($missing as $key) {
            error_log(
                "[CONFIG-SCHEMA] expected key '$key' missing from $path "
                . '— re-run `levels emit-config`'
            );
        }
    }

    private static function log_fallback_once(string $reason): void
    {
        if (self::$fallback_logged) {
            return;
        }
        self::$fallback_logged = true;
        error_log('[CONFIG-FALLBACK] ' . $reason . ' — using getenv() chain');
    }

    /**
     * Return the raw JSON value for $key, or getenv(strtoupper($key)),
     * or $default.
     *
     * @param mixed $default
     * @return mixed
     */
    public static function get(string $key, $default = null)
    {
        $cfg = self::get_singleton();
        if (array_key_exists($key, $cfg->data)) {
            return $cfg->data[$key];
        }
        $env = getenv(strtoupper($key));
        if ($env !== false && $env !== '') {
            return $env;
        }
        return $default;
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
}
