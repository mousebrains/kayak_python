import { defineConfig } from '@playwright/test';

/**
 * Playwright config for the JS smoke-test tier.
 *
 * See docs/PLAN_js_smoke_tests.md for the per-phase rationale. Two
 * things deliberately *not* used here, despite being defaults in
 * Playwright examples:
 *
 *   1. `webServer` block — Playwright's webServer launches the
 *      command *before* globalSetup runs, which means the spawned
 *      `php -S` can't see the `SQLITE_PATH` we mint in globalSetup.
 *      We own the server lifecycle inside globalSetup/globalTeardown
 *      instead. Same pattern as tests/php/IntegrationTestCase.php.
 *   2. `baseURL` driven by env var — Playwright loads this config
 *      *before* globalSetup runs, so any env var set in globalSetup
 *      would arrive too late. We hardcode `http://127.0.0.1:8000`;
 *      globalSetup binds the same port. Override with
 *      `KAYAK_TEST_PORT` if 8000 is occupied (globalSetup honors it).
 *
 * Tile-load timing note for future contributors: do NOT add
 * `await page.waitForLoadState('networkidle')` to any spec — Leaflet
 * fetches tiles from upstream CDNs (OpenTopoMap, OSM, Esri); waiting
 * for networkidle will flake when those servers are slow. `page.goto`
 * resolves on the `'load'` event (fires before tiles finish) which is
 * the right boundary for "did JS init crash?" assertions.
 */
export default defineConfig({
  testDir: 'tests/js',
  workers: 1, // php -S is single-threaded; serial execution avoids races.
  globalSetup: './tests/js/global-setup.ts',
  globalTeardown: './tests/js/global-teardown.ts',
  use: {
    viewport: { width: 1280, height: 720 },
    baseURL: 'http://127.0.0.1:8000',
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
});
