<?php

declare(strict_types=1);

require_once __DIR__ . '/IntegrationTestCase.php';
require_once __DIR__ . '/../../php/includes/pubhash.php';

/**
 * Integration coverage for source.php's detail view, focused on the
 * Phase-2 id-display cleanups:
 *  - the source's own `ID` meta row is gone from the detail table;
 *  - the "Associated Gauges" table dropped its internal-id column but
 *    keeps the external "USGS ID" column.
 *
 * Seed: one source linked (via gauge_source) to one gauge carrying a
 * location + usgs_id, so both the detail meta table and the associated-
 * gauges table render. ?h= addresses the source by its base-62 handle.
 */
final class SourceIntegrationTest extends IntegrationTestCase
{
    private const SOURCE_ID = 8001;
    private const GAUGE_ID = 8501;

    protected static function seedDatabase(PDO $db): void
    {
        $db->prepare('INSERT INTO source (id, name, agency) VALUES (?, ?, ?)')
            ->execute([self::SOURCE_ID, 'SRCINT_TEST', 'USGS']);

        $db->prepare('INSERT INTO gauge (id, name, location, usgs_id) VALUES (?, ?, ?, ?)')
            ->execute([self::GAUGE_ID, 'SRCINT_GAUGE', 'Marmot', '14128870']);

        $db->prepare('INSERT INTO gauge_source (gauge_id, source_id) VALUES (?, ?)')
            ->execute([self::GAUGE_ID, self::SOURCE_ID]);
    }

    public function testDetailDropsIdMetaRow(): void
    {
        $resp = $this->request('/source.php', ['h' => pubhash_encode(self::SOURCE_ID)]);

        $this->assertSame(200, $resp['status']);
        // The source detail rendered (name in the desc-table, which is present).
        $this->assertStringContainsString('SRCINT_TEST', $resp['body']);
        $this->assertStringContainsString('<td>Name</td>', $resp['body']);
        // ...but the internal-id meta row was dropped — no plain "ID" label cell.
        $this->assertStringNotContainsString('<td>ID</td>', $resp['body']);
    }

    public function testAssociatedGaugesDropsIdColumnKeepsUsgsId(): void
    {
        $resp = $this->request('/source.php', ['h' => pubhash_encode(self::SOURCE_ID)]);

        $this->assertSame(200, $resp['status']);
        $this->assertStringContainsString('Associated Gauges', $resp['body']);
        // Header dropped the leading internal-ID column; the external USGS ID stays.
        $this->assertStringContainsString(
            '<tr><th>Name</th><th>Location</th><th>USGS ID</th></tr>',
            $resp['body'],
        );
        // The linked gauge renders with its USGS id (the retained column's value).
        $this->assertStringContainsString('SRCINT_GAUGE', $resp['body']);
        $this->assertStringContainsString('14128870', $resp['body']);
    }
}
