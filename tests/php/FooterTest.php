<?php

declare(strict_types=1);

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../../src/kayak/web/php/includes/footer.php';

final class FooterTest extends TestCase
{
    protected function tearDown(): void
    {
        Config::install_for_tests([]);
    }

    public function testFooterReadsDataLicenseLabel(): void
    {
        Config::install_for_tests(['data_license' => ['label' => 'Open Data License 1.0']]);

        ob_start();
        include_footer();
        $html = (string)ob_get_clean();

        $this->assertStringContainsString('Data: <a href="/LICENSE-DATA.txt">Open Data License 1.0</a>', $html);
        $this->assertStringNotContainsString('Data: <a href="/LICENSE-DATA.txt">CC BY-NC 4.0</a>', $html);
    }

    public function testFooterEscapesDataLicenseLabel(): void
    {
        Config::install_for_tests(['data_license' => ['label' => 'License <script>alert(1)</script>']]);

        ob_start();
        include_footer();
        $html = (string)ob_get_clean();

        $this->assertStringContainsString('License &lt;script&gt;alert(1)&lt;/script&gt;', $html);
        $this->assertStringNotContainsString('<script>alert(1)</script>', $html);
    }
}
