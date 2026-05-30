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
// Loose-coupled to feature-map.js via a non-public
// convention: each map IIFE stashes its Leaflet instance on
// el._kayakMap. Resolved lazily on first mousemove (not at hydration)
// so the map IIFE has had a chance to run; if neither map is present
// we degrade silently to chart-only behavior.
(function () {
  'use strict';

  function hydrate(chart) {
    let payload;
    try {
      payload = JSON.parse(chart.dataset.profile);
    } catch (_e) {
      return;
    }
    if (!payload?.samples || payload.samples.length < 2) return;

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

    // Locate the Leaflet map exposed by static/feature-map.js (description,
    // gauge, and reach detail pages) on the first mousemove — by then
    // the map IIFE has run, even if both scripts were `defer`-loaded.
    let leafletMap = null;
    let mapHoverMarker = null;
    function getMap() {
      if (leafletMap) return leafletMap;
      const el =
        document.getElementById('feature-map') ||
        document.getElementById('reach-map');
      if (el?._kayakMap) {
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
      anchors.push({
        d_mi: xMax,
        lat: payload.takeout.lat,
        lon: payload.takeout.lon,
      });
    }

    function interpolateLatLon(dMi) {
      // Linear-interpolate (lat, lon) between adjacent anchors. The river
      // path between anchors isn't a straight line on the map, but anchors
      // are dense enough (every dl_mi ≈ 0.2 mi) that interp tracks the
      // channel closely; at the endpoints we anchor on the true put-in /
      // take-out so the dot reaches all the way to mile 0 and mile length.
      if (anchors.length === 0) return null;
      if (anchors.length === 1)
        return { lat: anchors[0].lat, lon: anchors[0].lon };
      if (dMi <= anchors[0].d_mi)
        return { lat: anchors[0].lat, lon: anchors[0].lon };
      const last = anchors[anchors.length - 1];
      if (dMi >= last.d_mi) return { lat: last.lat, lon: last.lon };
      let lo = 0,
        hi = anchors.length - 1;
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

    // --- Map dot rides the actual drawn trace (reach.geom) -----------------
    // Prefer the polyline the map already draws over the gradient-sample
    // anchors above: across a flat reservoir the analysis collapses the whole
    // pool into one coarse, insignificant bin, so there are no intermediate
    // anchors and interpolateLatLon() cuts a straight chord across the curve
    // (the dot left the trace ~mile 2.7 on Canyon Creek / id=419). feature-map.js
    // draws the geom from data-track="[[lat,lon],...]"; reuse it and place the
    // dot at the matching arc-length so it follows every bend to the take-out.
    function haversineMi(a, b) {
      const R = 3958.7613; // mean earth radius, miles
      const rad = Math.PI / 180;
      const dLat = (b[0] - a[0]) * rad;
      const dLon = (b[1] - a[1]) * rad;
      const lat1 = a[0] * rad;
      const lat2 = b[0] * rad;
      const s =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
      return 2 * R * Math.asin(Math.sqrt(s));
    }
    function readGeomTrack() {
      const mapEl =
        document.getElementById('feature-map') ||
        document.getElementById('reach-map');
      if (!mapEl?.dataset.track) return null;
      let pts;
      try {
        pts = JSON.parse(mapEl.dataset.track);
      } catch (_e) {
        return null;
      }
      if (!Array.isArray(pts) || pts.length < 2) return null;
      const cum = [0];
      for (let i = 1; i < pts.length; i++) {
        cum[i] = cum[i - 1] + haversineMi(pts[i - 1], pts[i]);
      }
      const total = cum[cum.length - 1];
      if (!(total > 0)) return null;
      return { pts, cum, total };
    }
    const geomTrack = readGeomTrack();

    function trackLatLon(dMi) {
      if (!geomTrack) return null;
      // d_mi is distance-from-put-in along the trace — the same
      // parameterization as the geom's cumulative length — so the axis
      // fraction maps 1:1 onto arc-length along the drawn polyline.
      const span = xMax - xMin || 1;
      const f = (dMi - xMin) / span;
      const pts = geomTrack.pts;
      if (f <= 0) return { lat: pts[0][0], lon: pts[0][1] };
      if (f >= 1) {
        const last = pts[pts.length - 1];
        return { lat: last[0], lon: last[1] };
      }
      const target = f * geomTrack.total;
      const cum = geomTrack.cum;
      let lo = 0;
      let hi = cum.length - 1;
      while (lo + 1 < hi) {
        const mid = (lo + hi) >> 1;
        if (cum[mid] <= target) lo = mid;
        else hi = mid;
      }
      const a = pts[lo];
      const b = pts[lo + 1];
      const segLen = cum[lo + 1] - cum[lo];
      const t = segLen > 0 ? (target - cum[lo]) / segLen : 0;
      return { lat: a[0] + t * (b[0] - a[0]), lon: a[1] + t * (b[1] - a[1]) };
    }

    // Elevation anchors (river mile -> ft), pinned to put-in / take-out so the
    // readout matches the drawn line. Null when the reach has no elevation data.
    const elevAnchors = payload.elev ? [{ d: 0, e: payload.elev.putin }] : null;
    if (elevAnchors) {
      for (const s of samples) {
        if (s.elev_ft != null) elevAnchors.push({ d: s.d_mi, e: s.elev_ft });
      }
      elevAnchors.push({ d: xMax, e: payload.elev.takeout });
    }

    function interpolateElev(dMi) {
      if (!elevAnchors) return null;
      if (dMi <= elevAnchors[0].d) return elevAnchors[0].e;
      const last = elevAnchors[elevAnchors.length - 1];
      if (dMi >= last.d) return last.e;
      let lo = 0,
        hi = elevAnchors.length - 1;
      while (lo + 1 < hi) {
        const mid = (lo + hi) >> 1;
        if (elevAnchors[mid].d <= dMi) lo = mid;
        else hi = mid;
      }
      const a = elevAnchors[lo];
      const b = elevAnchors[lo + 1];
      const span = b.d - a.d;
      const t = span > 0 ? (dMi - a.d) / span : 0;
      return a.e + t * (b.e - a.e);
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
      const yRange = yMax - yMin || 1;
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
        const elev = interpolateElev(dMi);
        const elevText =
          elev != null ? Math.round(elev).toLocaleString() + ' ft, ' : '';
        titleEl.textContent =
          'mi ' +
          dMi.toFixed(2) +
          ': ' +
          elevText +
          Math.round(s.grad_ft_per_mi) +
          ' ft/mi ' +
          windowText;
      }

      // Ride the drawn geom trace when present; fall back to the sample
      // anchors only for a chart with no companion map track.
      const ll = trackLatLon(dMi) || interpolateLatLon(dMi);
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
