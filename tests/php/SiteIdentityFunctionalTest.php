<?php

declare(strict_types=1);

require_once __DIR__ . '/FunctionalTestCase.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/config.php';
require_once __DIR__ . '/../../src/kayak/web/php/includes/header.php';

/**
 * S3a: the shared header reads dataset site identity (the emit-config `site`
 * block) for og:site_name + theme-color, falling back to the engine defaults
 * when no site.yaml is configured. Rendered in-process; no DB rows needed
 * (editor_feature is off, so render_nav never touches the DB).
 */
final class SiteIdentityFunctionalTest extends FunctionalTestCase
{
    protected static function seedDatabase(PDO $db): void
    {
        // No rows needed — the header renders identity from Config, not the DB.
    }

    private function renderHeader(): string
    {
        ob_start();
        include_header('Test Title');
        return (string)ob_get_clean();
    }

    public function testHeaderUsesDatasetSiteIdentity(): void
    {
        Config::install_for_tests([
            'editor_feature' => false,
            'site' => [
                'site_name' => 'Foo Levels',
                'org_name' => 'Foo Paddlers',
                'org_url' => 'https://foo.example',
                'brand_color' => '#abcdef',
                'brand_color_dark' => '#123456',
            ],
        ]);
        $html = $this->renderHeader();
        $this->assertStringContainsString('<meta property="og:site_name" content="Foo Levels">', $html);
        $this->assertStringContainsString(
            '<meta name="theme-color" content="#abcdef" media="(prefers-color-scheme: light)">',
            $html,
        );
        $this->assertStringContainsString(
            '<meta name="theme-color" content="#123456" media="(prefers-color-scheme: dark)">',
            $html,
        );
        $this->assertStringContainsString(
            '<a href="https://foo.example" rel="noopener" target="_blank">FP</a>',
            $html,
        );
        $this->assertStringContainsString('<h1><a href="/index.html">Foo Levels</a></h1>', $html);
    }

    public function testHeaderFallsBackToEngineDefaults(): void
    {
        // No `site` block (a dataset without site.yaml) → generic engine values.
        Config::install_for_tests(['editor_feature' => false]);
        $html = $this->renderHeader();
        $this->assertStringContainsString(
            '<meta property="og:site_name" content="River Levels">',
            $html,
        );
        $this->assertStringContainsString('content="#1b5591"', $html);
        $this->assertStringContainsString('content="#0d3057"', $html);
        $this->assertStringContainsString(
            '<a href="https://example.com" rel="noopener" target="_blank">Kayak</a>',
            $html,
        );
        $this->assertStringContainsString('<h1><a href="/index.html">River Levels</a></h1>', $html);
    }

    public function testHeaderEscapesSiteNameAtRender(): void
    {
        // Defense-in-depth: SiteConfig already rejects HTML metacharacters in
        // site_name, but the header must also escape at the render site — so a
        // hand-tampered runtime-config (bypassing validate-dataset) can't break
        // out of the content="…" attribute. install_for_tests skips SiteConfig.
        Config::install_for_tests([
            'editor_feature' => false,
            'site' => ['site_name' => 'Evil" onmouseover=x <script>'],
        ]);
        $html = $this->renderHeader();
        $this->assertStringContainsString(
            '<meta property="og:site_name" content="Evil&quot; onmouseover=x &lt;script&gt;">',
            $html,
        );
        $this->assertStringContainsString(
            '<h1><a href="/index.html">Evil&quot; onmouseover=x &lt;script&gt;</a></h1>',
            $html,
        );
        $this->assertStringNotContainsString('Evil" onmouseover', $html);  // no raw break-out
    }
}
