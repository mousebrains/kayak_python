// Cursor-linked gradient profile renderer.
//
// For each <svg class="gradient-profile-chart"> on the page, parse its
// data-profile payload (samples + axis metadata) and wire up mousemove
// handlers so:
//   * a vertical cursor line + sub-caption update with the gradient,
//     window width, and significance at the hovered river mile;
//   * a hover marker on the companion Leaflet map (#feature-map) jumps
//     to the matching (lat, lon).
//
// Loose-coupled to feature-map.js via a non-public convention: the map
// IIFE stashes its Leaflet instance on el._kayakMap. If the map isn't
// initialised yet at chart-hydration time we poll briefly; if it never
// shows up we just degrade silently to the chart-only tooltip.
(function(){
'use strict';

function hydrate(chart){
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

  const NS = 'http://www.w3.org/2000/svg';
  const cursor = document.createElementNS(NS, 'line');
  cursor.setAttribute('y1', String(m.mt));
  cursor.setAttribute('y2', String(m.mt + m.ph));
  cursor.setAttribute('stroke', '#222');
  cursor.setAttribute('stroke-width', '1');
  cursor.setAttribute('stroke-dasharray', '3,2');
  cursor.style.display = 'none';
  cursor.setAttribute('pointer-events', 'none');
  chart.appendChild(cursor);

  const dot = document.createElementNS(NS, 'circle');
  dot.setAttribute('r', '3');
  dot.setAttribute('fill', '#d32f2f');
  dot.setAttribute('stroke', '#fff');
  dot.setAttribute('stroke-width', '1');
  dot.style.display = 'none';
  dot.setAttribute('pointer-events', 'none');
  chart.appendChild(dot);

  const caption = document.createElementNS(NS, 'text');
  caption.setAttribute('text-anchor', 'middle');
  caption.setAttribute('font-size', '11');
  caption.setAttribute('fill', '#333');
  caption.style.display = 'none';
  caption.setAttribute('pointer-events', 'none');
  chart.appendChild(caption);

  // Locate the Leaflet map exposed by static/feature-map.js. Wait briefly
  // since the map IIFE runs after `defer`-loaded scripts, which may not
  // have executed by the time this chart's mousemove fires.
  let leafletMap = null;
  let mapHoverMarker = null;
  function getMap(){
    if (leafletMap) return leafletMap;
    const el = document.getElementById('feature-map') ||
               document.getElementById('reach-map');
    if (el && el._kayakMap) {
      leafletMap = el._kayakMap;
    }
    return leafletMap;
  }
  function placeMapDot(lat, lon){
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
  function clearMapDot(){
    if (mapHoverMarker) {
      const map = getMap();
      if (map) map.removeLayer(mapHoverMarker);
      mapHoverMarker = null;
    }
  }

  // Index sample by d_mi for fast lookup (linear binary search).
  function findSampleIndex(dMi){
    let lo = 0, hi = samples.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (samples[mid].d_mi < dMi) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  }

  function onMove(evt){
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
    const sig = s.significant ? '' : ' (below noise floor)';
    caption.textContent = 'mi ' + s.d_mi.toFixed(2)
      + ': ' + Math.round(s.grad_ft_per_mi) + ' ft/mi'
      + ' (window ' + s.w_mi.toFixed(2) + ' mi' + sig + ')';
    // Position caption — clamp away from edges.
    const tx = Math.max(m.ml + 60, Math.min(m.w - m.mr - 60, xPx));
    caption.setAttribute('x', String(tx));
    caption.setAttribute('y', String(m.mt - 4));
    caption.style.display = '';

    placeMapDot(s.lat, s.lon);
  }

  function onLeave(){
    cursor.style.display = 'none';
    dot.style.display = 'none';
    caption.style.display = 'none';
    clearMapDot();
  }

  chart.addEventListener('mousemove', onMove);
  chart.addEventListener('mouseleave', onLeave);
}

function hydrateAll(){
  document.querySelectorAll('.gradient-profile-chart').forEach(hydrate);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', hydrateAll);
} else {
  hydrateAll();
}
})();
