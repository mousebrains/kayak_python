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

  function findActiveWindow(dMi) {
    // Pick the bar whose [d_mi - w_mi/2, d_mi + w_mi/2] span contains dMi.
    // Bars are non-overlapping (the analysis emits one sample per
    // non-overlapping window), so this is unambiguous in the interior.
    // Outside the union of spans (rare — before first bar's left edge
    // or after the last bar's right edge), clamp to the nearest end.
    for (let i = 0; i < samples.length; i++) {
      if (dMi < samples[i].d_mi + samples[i].w_mi / 2) return i;
    }
    return samples.length - 1;
  }

  // Build an "anchor list" of (d_mi, lat, lon) covering the full reach.
  // Put-in (d_mi = 0) and take-out (d_mi = xMax) come from payload.putin /
  // payload.takeout so the map dot tracks all the way to the endpoints,
  // not just between bin centres. Intermediate anchors are sample bin
  // centres. Without the endpoint anchors, hovering in the first or last
  // half-bar would clamp the dot to the nearest bin centre — looked like
  // the dot stopped moving.
  const anchors = [];
  if (payload.putin && payload.putin.lat != null) {
    anchors.push({ d_mi: 0, lat: payload.putin.lat, lon: payload.putin.lon });
  }
  for (const s of samples) {
    if (s.lat != null && s.lon != null) {
      anchors.push({ d_mi: s.d_mi, lat: s.lat, lon: s.lon });
    }
  }
  if (payload.takeout && payload.takeout.lat != null) {
    anchors.push({ d_mi: xMax, lat: payload.takeout.lat, lon: payload.takeout.lon });
  }

  function interpolateLatLon(dMi) {
    // Linear-interpolate (lat, lon) between adjacent anchors. The river
    // path between anchors isn't a straight line on the map, but anchors
    // are dense enough (every dl_mi ≈ 0.2 mi) that interp tracks the
    // channel closely; at the endpoints we anchor on the true put-in /
    // take-out so the dot reaches all the way to mile 0 and mile length.
    if (anchors.length === 0) return null;
    if (anchors.length === 1) return { lat: anchors[0].lat, lon: anchors[0].lon };
    if (dMi <= anchors[0].d_mi) return { lat: anchors[0].lat, lon: anchors[0].lon };
    const last = anchors[anchors.length - 1];
    if (dMi >= last.d_mi) return { lat: last.lat, lon: last.lon };
    let lo = 0, hi = anchors.length - 1;
    while (lo + 1 < hi) {
      const mid = (lo + hi) >> 1;
      if (anchors[mid].d_mi <= dMi) lo = mid;
      else hi = mid;
    }
    const a = anchors[lo];
    const b = anchors[lo + 1];
    const span = b.d_mi - a.d_mi;
    const t = span > 0 ? (dMi - a.d_mi) / span : 0;
    return {
      lat: a.lat + t * (b.lat - a.lat),
      lon: a.lon + t * (b.lon - a.lon),
    };
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
    const idx = findActiveWindow(dMi);
    const s = samples[idx];
    if (!s) return;

    // Cursor follows the mouse smoothly across the whole reach (not
    // snapped to bar centres). The dot sits on the active bar's top
    // (gradient is a step function across windows, so y stays constant
    // within a bar and steps as the cursor crosses into the next one).
    const yRange = (yMax - yMin) || 1;
    const yPx = m.mt + ((yMax - s.grad_ft_per_mi) / yRange) * m.ph;

    cursor.setAttribute('x1', String(xView));
    cursor.setAttribute('x2', String(xView));
    cursor.style.display = '';
    dot.setAttribute('cx', String(xView));
    dot.setAttribute('cy', String(yPx));
    dot.style.display = '';

    if (titleEl) {
      // Readout reflects the active bar's value at the cursor mile.
      // For significant samples, w_mi is the smallest window where the
      // drop cleared the 3σ threshold — "window" reads as a fixed-
      // resolution claim. For insignificant samples, w_mi is the
      // clamped walk distance until the algorithm gave up (often the
      // whole remaining reach), so "integrated over" avoids implying
      // it's a chosen scale.
      const windowText = s.significant
        ? '(window ' + s.w_mi.toFixed(2) + ' mi)'
        : '(integrated over ' + s.w_mi.toFixed(2) + ' mi, below noise floor)';
      titleEl.textContent = 'mi ' + dMi.toFixed(2)
        + ': ' + Math.round(s.grad_ft_per_mi) + ' ft/mi '
        + windowText;
    }

    const ll = interpolateLatLon(dMi);
    if (ll) placeMapDot(ll.lat, ll.lon);
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
