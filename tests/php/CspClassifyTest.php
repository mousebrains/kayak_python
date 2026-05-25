<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../php/includes/csp_classify.php';

/**
 * csp_classify() — the _internal dashboard's "likely source" bucketing.
 *
 * The headline case is the regression that motivated the rewrite: a Google
 * transcoding proxy injects an inline <script> into a page, the browser reports
 * the violation against the *document* URL (no real script file exists), and
 * the old heuristic mislabeled it "Same-origin (our code)" purely because the
 * source_file was on our origin. It must now read "Injected (proxy/extension)".
 */
final class CspClassifyTest extends TestCase
{
    /** The exact shape of the 2026-05-25 google-proxy burst in csp.log. */
    public function test_injected_inline_from_proxy_is_not_our_code(): void
    {
        $report = [
            'violated'     => 'script-src-elem',
            'blocked'      => 'inline',
            'source_file'  => 'https://levels.wkcc.org/',
            'document_uri' => 'https://levels.wkcc.org/',
            'line'         => 1,
        ];
        $this->assertSame('Injected (proxy/extension)', csp_classify($report));
        $this->assertNotSame('Same-origin (our code)', csp_classify($report));
    }

    public function test_injected_eval_and_wasm_eval_same_origin_doc(): void
    {
        foreach (['eval', 'wasm-eval'] as $blocked) {
            $this->assertSame('Injected (proxy/extension)', csp_classify([
                'blocked'      => $blocked,
                'source_file'  => 'https://levels.wkcc.org/reach.php',
                'document_uri' => 'https://levels.wkcc.org/reach.php',
            ]), "blocked=$blocked");
        }
    }

    /** source_file == document modulo a query string the browser dropped. */
    public function test_injected_match_ignores_query_string(): void
    {
        $this->assertSame('Injected (proxy/extension)', csp_classify([
            'blocked'      => 'inline',
            'source_file'  => 'https://levels.wkcc.org/description.php',
            'document_uri' => 'https://levels.wkcc.org/description.php?id=42',
        ]));
    }

    /** A real same-origin asset (path past the bare page) is genuinely ours. */
    public function test_real_same_origin_asset_is_our_code(): void
    {
        $this->assertSame('Same-origin (our code)', csp_classify([
            'blocked'      => 'eval',
            'source_file'  => 'https://levels.wkcc.org/static/filters.js',
            'document_uri' => 'https://levels.wkcc.org/reach.php',
            'line'         => 42,
        ]));
    }

    public function test_extension_schemes(): void
    {
        $this->assertSame('Firefox extension', csp_classify([
            'source_file' => 'moz-extension://abc/inject.js', 'blocked' => 'inline',
        ]));
        $this->assertSame('Chrome/Edge extension', csp_classify([
            'source_file' => 'chrome-extension://abc/inject.js', 'blocked' => 'inline',
        ]));
        $this->assertSame('Safari extension', csp_classify([
            'source_file' => 'safari-extension://abc/inject.js', 'blocked' => 'inline',
        ]));
    }

    public function test_ad_blocker(): void
    {
        $this->assertSame('Ad blocker', csp_classify([
            'source_file'  => 'https://cdn.example/ublock-content.js',
            'blocked'      => 'inline',
            'document_uri' => 'https://levels.wkcc.org/',
        ]));
    }

    public function test_browser_internal_with_and_without_blocked(): void
    {
        $this->assertSame('Browser internal (wasm-eval)', csp_classify([
            'source_file' => '', 'blocked' => 'wasm-eval',
        ]));
        $this->assertSame('Browser internal', csp_classify([
            'source_file' => '', 'blocked' => '',
        ]));
    }

    /** Third-party script, concrete blocked URL → neither ours nor injected. */
    public function test_cross_origin_other(): void
    {
        $this->assertSame('Other', csp_classify([
            'source_file'  => 'https://cdn.thirdparty.com/widget.js',
            'blocked'      => 'https://evil.example/x.js',
            'document_uri' => 'https://levels.wkcc.org/',
        ]));
    }

    /** Legacy Reporting-API key (blocked_uri) still resolves via the fallback. */
    public function test_legacy_blocked_uri_key_fallback(): void
    {
        $this->assertSame('Injected (proxy/extension)', csp_classify([
            'blocked_uri'  => 'inline',
            'source_file'  => 'https://levels.wkcc.org/',
            'document_uri' => 'https://levels.wkcc.org/',
        ]));
    }
}
