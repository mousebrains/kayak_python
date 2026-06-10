import { test, expect, type Page } from '@playwright/test';

/**
 * JS smoke tests — assert pages load and JS initializes without
 * throwing. Scope is page-load only; tests do NOT click filter
 * pills, drag the map, hover sparklines, or submit forms (see
 * docs/done/PLAN_js_smoke_tests.md Phase 3 scoping).
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
  // at src/kayak/web/php/reach.php:193 — robust to data presence (init-db'd DB has
  // zero reaches, so the empty-state branch fires).
  await expect(page.locator('body')).toContainText('reaches matching');
  expect(errors).toEqual([]);
});

test('/Oregon.html loads with no JS errors', async ({ page }) => {
  // Per-state landing page emitted by `levels build` from the explicit
  // region.yaml fixture in global-setup.ts. No internal levels table, so no
  // filter-bar — those live on /gauges.html and /index.html. The page does
  // pull in `scroll-indicator.js`; assert the page loads + cross-link anchor
  // renders + no JS errors fire.
  const errors = captureJsErrors(page);

  const resp = await page.goto('/Oregon.html');
  expect(resp?.status()).toBe(200);
  // The "Live Oregon gauges" cross-link is one of the four anchors
  // _build_placeholder_page emits — its presence confirms both that
  // the landing-page builder ran and that the link points at the
  // fragment-filtered all-states gauges view.
  await expect(page.locator('a[href="/gauges.html#st=Oregon"]')).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('/gauges.html#st=Oregon loads with the filter-bar UI', async ({ page }) => {
  // The fragment-filter entry point that replaces the previous
  // gauges.oregon.html artifact. filters.js reads the #st= fragment
  // on load, the State filter pill is data-driven (so any state with
  // visible gauges auto-appears), and the all-states gauges table
  // renders the filter-bar UI that this test pins. With zero
  // observations in the init-db'd test DB the gauges table is empty
  // but the filter-bar still binds event handlers — the path that
  // historically broke under the `var → let` refactor in
  // docs/done/PLAN_js_cleanup.md Phase 3.
  const errors = captureJsErrors(page);

  const resp = await page.goto('/gauges.html#st=Oregon');
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
  //
  // **Load-bearing data dependency:** map.js:120 emits
  // `console.error('map data load failed:', e)` if reaches-geom.json
  // can't be fetched. Today `levels build` always writes that file
  // (empty-array JSON is still valid + serveable), so the toEqual([])
  // check holds. If a future build optimization adds "skip empty
  // static JSON," this test goes red for a non-bug — update the
  // build step in global-setup.ts or seed reach data before that
  // change lands.
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
