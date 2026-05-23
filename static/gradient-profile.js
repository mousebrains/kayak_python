// Cursor-linked gradient profile renderer.
//
// For each <svg class="gradient-profile-chart"> on the page, parse its
// data-profile payload (samples + axis metadata) and wire up mousemove
// handlers so:
//   * a vertical cursor line + chart dot mark the hovered river mile;
//   * the chart's title text is replaced with the readout "mi X.XX:
//     NNN ft/mi (window W mi[, below noise floor])" — held in the same
//     centered slot so it never gets clipped against the chart edges;
//   * a hover marker on the companion Leaflet map (#feature-map) jumps
//     to the matching (lat, lon).
//
// Loose-coupled to feature-map.js and reach-map.js via a non-public
// convention: each map IIFE stashes its Leaflet instance on
// el._kayakMap. Resolved lazily on first mousemove (not at hydration)
// so the map IIFE has had a chance to run; if neither map is present
// we degrade silently to chart-only behavior.
(function () {
'use strict';

function hydrate(chart) {
  let payload;
  try { payload = JSON.parse(chart.dataset.profile); }
  catch (e) { return; }
  if (!payload || !payload.samples || payload.samples.length < 2) return;

  const samples = payload.samples;
  const xMin = payload.x_min;
  const xMax = payload.x_max;
  const yMin = payload.y_min;
  const yMax = payload.y_max;
  const m = payload.margins;
  if (!m) return;

  // The PHP renderer emits a <text class="gp-title"> at top-center. Reuse
  // it as the readout slot — replacing its text on hover, restoring it
  // on leave. Avoids the truncation problem of a cursor-following caption.
  const titleEl = chart.querySelector('.gp-title');
  const titleOriginal = titleEl ? titleEl.textContent : '';

  const NS = 'http://www.w3.org/2000/svg';
  const cursor = document.createElementNS(NS, 'line');
  cursor.setAttribute('class', 'gp-cursor');
  cursor.setAttribute('y1', String(m.mt));
  cursor.setAttribute('y2', String(m.mt + m.ph));
  cursor.style.display = 'none';
  chart.appendChild(cursor);

  const dot = document.createElementNS(NS, 'circle');
  dot.setAttribute('class', 'gp-dot');
  dot.setAttribute('r', '4');
  dot.style.display = 'none';
  chart.appendChild(dot);

  // Locate the Leaflet map exposed by static/feature-map.js (gauge page)
  // or static/reach-map.js (reach page) on the first mousemove — by then
  // the map IIFE has run, even if both scripts were `defer`-loaded.
  let leafletMap = null;
  let mapHoverMarker = null;
  function getMap() {
    if (leafletMap) return leafletMap;
    const el = document.getElementById('feature-map') ||
               document.getElementById('reach-map');
    if (el && el._kayakMap) {
      leafletMap = el._kayakMap;
    }
    return leafletMap;
  }
  function placeMapDot(lat, lon) {
    const map = getMap();
    if (!map || typeof L === 'undefined') return;
    if (!mapHoverMarker) {
      mapHoverMarker = L.circleMarker([lat, lon], {
        radius: 6,
        color: '#d32f2f',
        weight: 2,
        fillColor: '#d32f2f',
        fillOpacity: 0.5,
        interactive: false,
      }).addTo(map);
    } else {
      mapHoverMarker.setLatLng([lat, lon]);
    }
  }
  function clearMapDot() {
    if (mapHoverMarker) {
      const map = getMap();
      if (map) map.removeLayer(mapHoverMarker);
      mapHoverMarker = null;
    }
  }

  function findSampleIndex(dMi) {
    // Binary search for the lowest index with d_mi >= dMi, then snap
    // to the actually-nearest of (idx-1, idx). Without the snap, hover
    // always picks the right-hand neighbor — visible jitter at bar
    // boundaries.
    let lo = 0, hi = samples.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (samples[mid].d_mi < dMi) lo = mid + 1;
      else hi = mid;
    }
    if (lo > 0 && (dMi - samples[lo - 1].d_mi) < (samples[lo].d_mi - dMi)) {
      return lo - 1;
    }
    return lo;
  }

  function onMove(evt) {
    // Convert client coords to SVG viewBox coords (chart width may differ
    // from SVG width due to responsive scaling).
    const rect = chart.getBoundingClientRect();
    const xClient = evt.clientX - rect.left;
    const viewBoxScale = m.w / rect.width;
    const xView = xClient * viewBoxScale;
    if (xView < m.ml || xView > m.ml + m.pw) {
      onLeave();
      return;
    }
    const dMi = xMin + ((xView - m.ml) / m.pw) * (xMax - xMin);
    const idx = findSampleIndex(dMi);
    const s = samples[idx];
    if (!s) return;

    const xPx = m.ml + ((s.d_mi - xMin) / (xMax - xMin)) * m.pw;
    const yRange = (yMax - yMin) || 1;
    const yPx = m.mt + ((yMax - s.grad_ft_per_mi) / yRange) * m.ph;

    cursor.setAttribute('x1', String(xPx));
    cursor.setAttribute('x2', String(xPx));
    cursor.style.display = '';
    dot.setAttribute('cx', String(xPx));
    dot.setAttribute('cy', String(yPx));
    dot.style.display = '';

    if (titleEl) {
      // For significant samples, w_mi is the smallest window where the
      // drop cleared the 3σ threshold — "window" reads as a fixed-
      // resolution claim. For insignificant samples, w_mi is the
      // clamped walk distance until the algorithm gave up (often the
      // whole remaining reach), so "integrated over" avoids implying
      // it's a chosen scale.
      const windowText = s.significant
        ? '(window ' + s.w_mi.toFixed(2) + ' mi)'
        : '(integrated over ' + s.w_mi.toFixed(2) + ' mi, below noise floor)';
      titleEl.textContent = 'mi ' + s.d_mi.toFixed(2)
        + ': ' + Math.round(s.grad_ft_per_mi) + ' ft/mi '
        + windowText;
    }

    placeMapDot(s.lat, s.lon);
  }

  function onLeave() {
    cursor.style.display = 'none';
    dot.style.display = 'none';
    if (titleEl) titleEl.textContent = titleOriginal;
    clearMapDot();
  }

  chart.addEventListener('mousemove', onMove);
  chart.addEventListener('mouseleave', onLeave);
}

function hydrateAll() {
  document.querySelectorAll('.gradient-profile-chart').forEach(hydrate);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', hydrateAll);
} else {
  hydrateAll();
}
})();
