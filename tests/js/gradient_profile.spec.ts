import { test, expect } from '@playwright/test';
import { resolve } from 'node:path';

/**
 * Interactive gradient-profile readout spec (PR #123 round 9).
 *
 * The PHP renderer intentionally leaves a blank span when the gradient trace
 * stops short of reach.length — a reservoir at the take-out has zero gradient
 * and emits no samples (svg_plot.php: the last bar is no longer stretched to
 * the take-out). The hover readout (static/gradient-profile.js findActiveWindow)
 * must MATCH that geometry: a hover over the blank tail reports "no gradient
 * data", not the last sample's value clamped across the gap.
 *
 * No PHP server / DB needed — we inject the JS against a hand-built payload via
 * setContent + addScriptTag, then dispatch a mousemove at a chosen river mile.
 */

const JS = resolve(__dirname, '../../static/gradient-profile.js');

// Samples cover ~0..1.625 mi of a 5 mi reach (x_max = 5), so [1.625, 5] is the
// blank reservoir tail. All margins are explicit so the mile<->pixel mapping is
// deterministic; m.w === the SVG width so the viewBox scale is 1.
const PAYLOAD = {
  x_min: 0,
  x_max: 5,
  y_min: 0,
  y_max: 130,
  margins: { w: 480, ml: 50, pw: 420, mt: 22, ph: 72 },
  samples: [
    { d_mi: 0.0, w_mi: 0.5, grad_ft_per_mi: 80, significant: true, lat: 44.1, lon: -122.0 },
    { d_mi: 0.5, w_mi: 0.25, grad_ft_per_mi: 120, significant: true, lat: 44.11, lon: -122.01 },
    { d_mi: 1.0, w_mi: 1.0, grad_ft_per_mi: 50, significant: true, lat: 44.12, lon: -122.02 },
    { d_mi: 1.5, w_mi: 0.25, grad_ft_per_mi: 40, significant: true, lat: 44.13, lon: -122.03 },
  ],
};

async function setup(page) {
  // JSON has no single quotes, so a single-quoted attribute needs no escaping.
  const profile = JSON.stringify(PAYLOAD);
  await page.setContent(
    `<svg class="gradient-profile-chart" width="480" height="120" data-profile='${profile}'>` +
      `<text class="gp-title">original title</text></svg>`,
  );
  await page.addScriptTag({ path: JS });
}

// Dispatch a mousemove at river-mile dMi and return the resulting readout +
// whether the gradient dot is shown. The clientX<->xView math cancels the
// element's rendered width, so it is robust to the headless layout.
async function hover(page, dMi: number): Promise<{ title: string; dotShown: boolean }> {
  return page.evaluate((dMi) => {
    const chart = document.querySelector('.gradient-profile-chart') as SVGSVGElement;
    const p = JSON.parse(chart.getAttribute('data-profile') as string);
    const m = p.margins;
    const xView = m.ml + ((dMi - p.x_min) / (p.x_max - p.x_min)) * m.pw;
    const rect = chart.getBoundingClientRect();
    const clientX = rect.left + (xView * rect.width) / m.w;
    chart.dispatchEvent(
      new MouseEvent('mousemove', { clientX, clientY: rect.top + 30, bubbles: true }),
    );
    const title = chart.querySelector('.gp-title')?.textContent ?? '';
    const dot = chart.querySelector('.gp-dot') as SVGElement | null;
    return { title, dotShown: !!dot && dot.style.display !== 'none' };
  }, dMi);
}

test('hover over a gradient bar reports its value with the dot shown', async ({ page }) => {
  await setup(page);
  const { title, dotShown } = await hover(page, 1.0); // inside the 50 ft/mi window
  expect(title).toContain('50 ft/mi');
  expect(dotShown).toBe(true);
});

test('hover over the blank reservoir tail reports no gradient data', async ({ page }) => {
  await setup(page);
  const { title, dotShown } = await hover(page, 4.0); // past the last bar (1.625 mi)
  expect(title).toContain('no gradient data');
  expect(title).not.toContain('ft/mi'); // not the clamped last-sample value
  expect(dotShown).toBe(false); // no gradient point drawn over a blank span
});
