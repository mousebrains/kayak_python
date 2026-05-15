/* Scroll-position indicator for overflow-scrolling strips.
 *
 * Toggles `data-overflow-left` and `data-overflow-right` attributes on any
 * element marked `[data-scroll-indicate]` based on its current scroll
 * position. CSS in style.css drives the visual fade off those attributes:
 *   - both attrs absent (no overflow OR JS not loaded yet): no fade
 *   - data-overflow-right only (scrolled to start): right-edge fade
 *   - data-overflow-left only (scrolled to end):    left-edge fade
 *   - both attrs present (mid-scroll):              fades on both edges
 *
 * Per docs/PLAN_map_and_ui_tweaks.md Item 5. Decision §6 accepts the
 * brief first-paint no-fade gap before the `defer`-loaded script
 * attaches (~1 KB, sub-100 ms on modern connections).
 *
 * Element-agnostic — any future scrollable strip (tab bar, pill row,
 * carousel) can opt in by adding `data-scroll-indicate`.
 */
(function () {
  'use strict';
  // Tolerance for "fully scrolled to edge". scrollWidth/clientWidth can
  // round to non-integer pixels on high-DPI displays; treating anything
  // within 2 px of an edge as flush avoids a flickering fade.
  const SLACK = 2;
  function update(el) {
    const max = el.scrollWidth - el.clientWidth;
    el.toggleAttribute('data-overflow-left', el.scrollLeft > SLACK);
    el.toggleAttribute('data-overflow-right', el.scrollLeft < max - SLACK);
  }
  // ResizeObserver catches viewport changes + content additions; scroll
  // event catches direct user scroll + browser-driven Tab-to-link auto-scroll.
  const ro = new ResizeObserver(function (entries) {
    entries.forEach(function (e) { update(e.target); });
  });
  document.querySelectorAll('[data-scroll-indicate]').forEach(function (el) {
    update(el);
    el.addEventListener('scroll', function () { update(el); }, { passive: true });
    ro.observe(el);
  });
})();
