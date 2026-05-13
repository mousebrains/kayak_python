import { test, expect, type Page } from '@playwright/test';

/**
 * JS smoke tests — assert pages load and JS initializes without
 * throwing. Scope is page-load only; tests do NOT click filter
 * pills, drag the map, hover sparklines, or submit forms (see
 * docs/PLAN_js_smoke_tests.md Phase 3 scoping).
 *
 * **Tile-load timing:** never `await page.waitForLoadState('networkidle')`
 * here. Leaflet fetches tiles from upstream CDNs (OpenTopoMap, OSM,
 * Esri); waiting for networkidle would flake against slow CDNs. The
 * default page.goto() resolves on the 'load' event (fires before
 * tiles finish), which is the right boundary for "did JS init crash?"
 * assertions.
 *
 * **Console-error capture:** each test attaches its own pageerror +
 * console-error listeners before navigation. Listener attachment is
 * synchronous and happens before page.goto's network request, so the
 * race window for missed pre-load errors is zero.
 */

/** Attach JS-error capture, return the accumulator array. */
function captureJsErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (err) => errors.push(`pageerror: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      errors.push(`console.error: ${msg.text()}`);
    }
  });
  return errors;
}

test('/reach.php?st=OR loads with no JS errors', async ({ page }) => {
  const errors = captureJsErrors(page);

  const resp = await page.goto('/reach.php?st=OR');
  expect(resp?.status()).toBe(200);
  // 'reaches matching' substring is asserted by the empty-state copy
  // at php/reach.php:193 — robust to data presence (init-db'd DB has
  // zero reaches, so the empty-state branch fires).
  await expect(page.locator('body')).toContainText('reaches matching');
  expect(errors).toEqual([]);
});
