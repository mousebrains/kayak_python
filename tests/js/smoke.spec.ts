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

test('/Oregon.html loads with no JS errors', async ({ page }) => {
  // Per-state HTML emitted by `levels build` — exercises levels.js +
  // filters.js + plot-hover.js. With zero observations in the init-db'd
  // test DB, the levels table is empty but the filter-bar UI still
  // renders and binds event handlers (the path that historically broke
  // under the `var → let` refactor in PLAN_js_cleanup.md Phase 3).
  const errors = captureJsErrors(page);

  const resp = await page.goto('/Oregon.html');
  expect(resp?.status()).toBe(200);
  await expect(page.locator('#filter-bar')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('/map.html loads with no JS errors', async ({ page }) => {
  // Leaflet map page — exercises map.js (the largest JS file and the
  // one most touched by the cleanup tier). Asserting on the Leaflet
  // container's existence confirms map.js ran past its init block
  // (the 5-loop `_mfCasing/_mfHit` rendering at map.js:276-299 needs
  // the container to construct successfully).
  const errors = captureJsErrors(page);

  const resp = await page.goto('/map.html');
  expect(resp?.status()).toBe(200);
  await expect(page.locator('.leaflet-container')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('/picker.php loads with no JS errors', async ({ page }) => {
  // Reach picker — exercises picker.js + filters.js + search-map.js.
  // Initial-load coverage only; smoke tests don't click state pills
  // so the search-map's lazy XHR fetch is never triggered.
  const errors = captureJsErrors(page);

  const resp = await page.goto('/picker.php');
  expect(resp?.status()).toBe(200);
  await expect(page.locator('#filter-bar')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('/gauge_picker.php loads with no JS errors', async ({ page }) => {
  // Gauge picker — exercises gauge_picker.js + filters.js. Same
  // initial-load coverage rationale as picker.php; the state-pill-
  // triggered XHR to fetch the gauge list is not exercised.
  const errors = captureJsErrors(page);

  const resp = await page.goto('/gauge_picker.php');
  expect(resp?.status()).toBe(200);
  await expect(page.locator('#filter-bar')).toHaveCount(1);
  expect(errors).toEqual([]);
});
