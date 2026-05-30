import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';

/**
 * Right-click map popup behavioral spec (R4.1, for #79).
 *
 * Proves the `feature-map.js` `contextmenu` -> lat/lon popup handler
 * (static/feature-map.js:503, `L.DomEvent.on(map.getContainer(),
 * 'contextmenu', ...)`) actually fires: a right-click on the Leaflet
 * map container opens an `.latlon-popup` (:538) containing the
 * formatted coordinate string (:509-511) and a Copy button (:520).
 *
 * **Why a /description.php route, not /map.html:** the handler is
 * loaded only by *detail* pages (description_detail.php:84-88 emits the
 * `leaflet.js` + `feature-map.js` script tags when `$has_map`), NOT by
 * the static /map.html (which loads map.js). description.php is a
 * *dynamic* page (queries the DB live), so a reach seeded AFTER the
 * one-time global-setup boot via `sqliteExec` IS served on the next
 * request.
 *
 * **Why a Put-in coordinate is load-bearing:** a coords-less reach
 * renders no map. A *start* coordinate alone lights it up --
 * description_detail.php:464-468 puts `latitude_start`/`longitude_start`
 * into `$map_points['Put-in']`; :506 (`count($map_points) >= 1`) ->
 * :509 `gm_render_map()` emits `#feature-map` (gauge_map.php:120),
 * which Leaflet upgrades to `.leaflet-container`. No geom needed.
 *
 * **Clipboard:** we assert the Copy button exists + the coordinate
 * text, NOT a clipboard *paste* -- reading the clipboard needs a
 * `clipboard-read` permission grant the harness doesn't set up, and
 * the handler's writeText (:522-535) degrades gracefully anyway.
 *
 * Shares the one global-setup PHP server with smoke/editor specs;
 * uses a Date.now() stamp so re-runs against the same DB never collide.
 */

const DB_PATH = (() => {
  const p = process.env.KAYAK_TEST_DBPATH;
  if (!p) throw new Error('KAYAK_TEST_DBPATH not set — global-setup must run first');
  return p;
})();

/** Run one or more SQL statements via the sqlite3 CLI against the test DB. */
function sqliteExec(sql: string): string {
  return execFileSync('sqlite3', ['-bail', DB_PATH], {
    input: sql,
    encoding: 'utf8',
  });
}

/**
 * Insert a reach carrying a Put-in coordinate (and no_show=0 so it's
 * publicly visible) and return the new id. Every reach column except
 * `id` is nullable or has `server_default 0`, so this minimal shape
 * inserts and renders the map.
 */
function seedReachWithPutin(name: string): number {
  const out = sqliteExec(`
    INSERT INTO reach (name, sort_name, display_name, river,
                       latitude_start, longitude_start, no_show)
      VALUES ('${name}', '${name.toLowerCase()}', '${name}', 'Test River',
              44.06, -121.31, 0);
    SELECT last_insert_rowid();
  `);
  return parseInt(out.trim(), 10);
}

test('right-click on the reach map opens a lat/lon popup with a Copy button', async ({ page }) => {
  const stamp = Date.now();
  const reachId = seedReachWithPutin(`R4.1 Map ${stamp}`);
  expect(reachId).toBeGreaterThan(0); // seed sanity

  // description.php is dynamic, so the just-seeded reach is served.
  const resp = await page.goto(`/description.php?id=${reachId}`);
  expect(resp?.status()).toBe(200);

  // Leaflet upgrades #feature-map into .leaflet-container once
  // feature-map.js runs its init — its presence confirms the map
  // (and thus the contextmenu listener) constructed.
  await page.waitForSelector('.leaflet-container');

  // Right-click the map. feature-map.js:503 listens via
  // L.DomEvent.on(container, 'contextmenu', ...) (a native listener on
  // the container), so a bubbled contextmenu fires it regardless of the
  // exact hit-tested child element.
  await page.locator('.leaflet-container').click({ button: 'right' });

  // The popup opens with the formatted coordinate string + Copy button.
  const popup = page.locator('.latlon-popup');
  await expect(popup).toBeVisible();
  // A "<lat>, <lon>" pair, each toFixed(6) (feature-map.js:509-511);
  // \d+\.\d+ matches a decimal-degree coordinate regardless of value.
  await expect(popup).toContainText(/-?\d+\.\d+/);
  await expect(popup.getByRole('button', { name: 'Copy' })).toBeVisible();
});
