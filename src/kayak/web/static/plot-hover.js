/* Hover/tap tooltip on description.php plots.
 *
 * HTML contract:
 *   Target selector: `.plot-container svg[data-series]`
 *   data-series JSON (emitted by src/kayak/web/php/includes/svg_plot.php):
 *     {
 *       kind:     "single" | "dual",
 *       points:   [[unix_ts, value], ...],        // already LTTB-downsampled
 *       label:    "Flow",                         // y_label split at "("
 *       unit:     "CFS",
 *       decimals: 0 | 1 | 2,                      // value format
 *       y_min: 6000, y_max: 14000,                // nice_axis bounds of Y
 *       margins: { ml, mr, mt, mb, w, h },        // SVG viewBox layout
 *       // dual only:
 *       rating:         [[gauge_ft, flow_cfs], ...],
 *       gauge_decimals: 1,
 *     }
 *
 * Behavior:
 *   - On pointermove over the plot area, shows a crosshair + marker on the
 *     polyline's nearest (by timestamp) point, and a sibling <div> tooltip
 *     positioned near the marker with timestamp + value(s).
 *   - Dual plot tooltip also shows the rated gauge height derived from the
 *     same empirical rating lookup the right axis uses.
 *   - Touch: pointerdown shows the tooltip; pointerleave hides it.
 *   - Fails silently if data-series is missing or malformed — the plot is
 *     unaffected.
 */
(function () {
  'use strict';

  const SVG_NS = 'http://www.w3.org/2000/svg';

  function fmtValue(v, decimals) {
    return v.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    const m = d.getMonth() + 1;
    const day = d.getDate();
    const h = d.getHours();
    const min = d.getMinutes();
    return m + '/' + day + ' ' + h + ':' + (min < 10 ? '0' + min : '' + min);
  }

  /* Inverse rating: flow cfs -> gauge ft. Mirrors PHP rate_flow_to_gauge. */
  function rateFlowToGauge(lookup, flowCfs) {
    const n = lookup.length;
    if (n === 0) return null;
    if (flowCfs <= lookup[0][1]) return lookup[0][0];
    if (flowCfs >= lookup[n - 1][1]) return lookup[n - 1][0];
    for (let i = 0; i < n - 1; i++) {
      const g1 = lookup[i][0],
        f1 = lookup[i][1];
      const g2 = lookup[i + 1][0],
        f2 = lookup[i + 1][1];
      if (f1 <= flowCfs && flowCfs <= f2) {
        if (f2 === f1) return g1;
        return g1 + ((g2 - g1) / (f2 - f1)) * (flowCfs - f1);
      }
    }
    return null;
  }

  /* Binary-search points (sorted by timestamp) for the index whose [0]
   * is closest to target. */
  function nearestIndex(points, target) {
    let lo = 0,
      hi = points.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (points[mid][0] < target) lo = mid + 1;
      else hi = mid;
    }
    if (
      lo > 0 &&
      Math.abs(points[lo - 1][0] - target) < Math.abs(points[lo][0] - target)
    ) {
      return lo - 1;
    }
    return lo;
  }

  function attach(svg) {
    const container = svg.closest('.plot-container');
    if (!container) return;

    let payload;
    try {
      payload = JSON.parse(svg.getAttribute('data-series'));
    } catch (_e) {
      return;
    }
    if (!payload?.points || payload.points.length < 2) return;

    const pts = payload.points;
    const m = payload.margins;
    const xMin = pts[0][0];
    const xMax = pts[pts.length - 1][0];
    const spanSec = xMax - xMin || 1;
    const yRange = payload.y_max - payload.y_min || 1;
    const plotLeft = m.ml;
    const plotRight = m.w - m.mr;
    const plotTop = m.mt;
    const plotBottom = m.h - m.mb;
    const plotWidth = plotRight - plotLeft;
    const plotHeight = plotBottom - plotTop;

    /* Build overlay DOM once. */
    const tooltip = document.createElement('div');
    tooltip.className = 'plot-tooltip';
    tooltip.hidden = true;
    container.appendChild(tooltip);

    const crosshair = document.createElementNS(SVG_NS, 'line');
    crosshair.setAttribute('class', 'plot-crosshair');
    crosshair.setAttribute('y1', plotTop);
    crosshair.setAttribute('y2', plotBottom);
    crosshair.setAttribute('visibility', 'hidden');
    svg.appendChild(crosshair);

    const marker = document.createElementNS(SVG_NS, 'circle');
    marker.setAttribute('class', 'plot-marker-flow');
    marker.setAttribute('r', '4');
    marker.setAttribute('visibility', 'hidden');
    svg.appendChild(marker);

    function hide() {
      tooltip.hidden = true;
      crosshair.setAttribute('visibility', 'hidden');
      marker.setAttribute('visibility', 'hidden');
    }

    function showAt(clientX, clientY) {
      /* clientX -> SVG local x via CTM inverse (handles CSS scaling / zoom). */
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      const pt = svg.createSVGPoint();
      pt.x = clientX;
      pt.y = clientY;
      const svgPt = pt.matrixTransform(ctm.inverse());
      if (
        svgPt.x < plotLeft ||
        svgPt.x > plotRight ||
        svgPt.y < plotTop ||
        svgPt.y > plotBottom
      ) {
        hide();
        return;
      }

      const xFrac = (svgPt.x - plotLeft) / plotWidth;
      const targetTs = xMin + xFrac * spanSec;
      const idx = nearestIndex(pts, targetTs);
      const ts = pts[idx][0];
      const val = pts[idx][1];

      const xPx = plotLeft + ((ts - xMin) / spanSec) * plotWidth;
      const yPx = plotTop + ((payload.y_max - val) / yRange) * plotHeight;

      marker.setAttribute('cx', xPx);
      marker.setAttribute('cy', yPx);
      marker.setAttribute('visibility', 'visible');
      crosshair.setAttribute('x1', xPx);
      crosshair.setAttribute('x2', xPx);
      crosshair.setAttribute('visibility', 'visible');

      /* Build tooltip text. */
      const lines = [fmtTime(ts)];
      let valLine = payload.label + ': ' + fmtValue(val, payload.decimals);
      if (payload.unit) valLine += ' ' + payload.unit;
      lines.push(valLine);
      if (payload.kind === 'dual' && payload.rating) {
        const g = rateFlowToGauge(payload.rating, val);
        if (g !== null) {
          lines.push(
            'Gage: ' + fmtValue(g, payload.gauge_decimals || 1) + ' ft',
          );
        }
      }
      tooltip.textContent = lines.join('\n');
      tooltip.hidden = false;

      /* Position tooltip near the marker in container-local CSS coords. */
      const svgRect = svg.getBoundingClientRect();
      const containerRect = container.getBoundingClientRect();
      const scaleX = svgRect.width / m.w;
      const scaleY = svgRect.height / m.h;
      const markerCssX = svgRect.left - containerRect.left + xPx * scaleX;
      const markerCssY = svgRect.top - containerRect.top + yPx * scaleY;

      let ttX = markerCssX + 8;
      let ttY = markerCssY - 8 - tooltip.offsetHeight;
      /* Clip right: flip left of marker. */
      if (ttX + tooltip.offsetWidth > containerRect.width) {
        ttX = markerCssX - tooltip.offsetWidth - 8;
      }
      /* Clip top: flip below marker. */
      if (ttY < 0) {
        ttY = markerCssY + 8;
      }
      if (ttX < 0) ttX = 0;
      tooltip.style.left = ttX + 'px';
      tooltip.style.top = ttY + 'px';
    }

    svg.addEventListener('pointermove', function (e) {
      showAt(e.clientX, e.clientY);
    });
    svg.addEventListener('pointerdown', function (e) {
      showAt(e.clientX, e.clientY);
    });
    svg.addEventListener('pointerleave', hide);
  }

  function init() {
    const svgs = document.querySelectorAll('.plot-container svg[data-series]');
    for (let i = 0; i < svgs.length; i++) attach(svgs[i]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
