"""HTML page-shell helpers: nav, footer, letter-nav, page wrappers."""

import html as html_mod
from datetime import UTC, datetime
from urllib.parse import quote as _urlquote

from kayak.dataset.region import get_region_config
from kayak.web.build._shared import (
    _FILTERS_JS_VERSION,
    _LEVELS_JS,
    _MAP_JS_VERSION,
    _STATE_ABBREVS,
    BRAND_COLOR,
    BRAND_COLOR_DARK,
    _editor_feature_on,
    _og_meta,
    _state_page_path,
)

# Per-state weather URLs + curated external-resource links moved to
# kayak.dataset.region (S3b-1); read here via get_region_config() so a dataset
# region.yaml can override them. Engine defaults reproduce the prior WKCC data.


def _presentation_states(states: list[str]) -> list[str]:
    """The states that get nav buttons + a landing page + a sitemap entry (S3b-2).

    The union of *states* (reach-states from ``all_state_names()``) and the dataset
    region config's states: a state appears if it has visible reaches OR curated
    region links (incl. a gauges-only / reach-less state). Centralized so the nav,
    the landing-page loop, and the sitemap can't drift to inconsistent sets.
    """
    return sorted(set(states) | set(get_region_config().states))


def _build_nav(
    states: list[str],
    active_state: str = "",
    active_page: str = "",
    picker_kind: str = "reach",
) -> str:
    """Build abbreviation-based nav bar; each state links to its {State}.html page.

    The all-reaches levels table lives at /index.html and is reached via the
    "River Levels" h1 home link. The per-state pages (Oregon.html etc.) are
    curated link indexes of external resources (American Whitewater,
    Dreamflows, agency dashboards).

    active_page highlights a non-state link ("map" or "gauges") so the user
    has a visual anchor on the corresponding page. picker_kind picks which
    of /picker.php (reach) or /gauge_picker.php (gauge) the single "Picker"
    link points at — the page's own context decides, so reach-y pages get
    the reach picker and gauge-y pages get the gauge picker.
    """
    links: list[str] = []
    map_cls = ' class="active"' if active_page == "map" else ""
    gauges_cls = ' class="active"' if active_page == "gauges" else ""
    links.append(f'<a href="/map.html"{map_cls}>Map</a>')
    links.append(f'<a href="/gauges.html"{gauges_cls}>Gauges</a>')
    # State buttons come from the presentation set (reach-states + region config;
    # S3b-2). Names are gated to ASCII letter words at the dataset boundary, but
    # escape the rendered label as defense-in-depth (a no-op for safe names).
    for s in _presentation_states(states):
        abbrev = html_mod.escape(_STATE_ABBREVS.get(s, s))
        cls = ' class="active"' if s == active_state else ""
        links.append(f'<a href="{_state_page_path(s)}"{cls}>{abbrev}</a>')
    # Picker links carry ?state=<full-name> when active_state is set,
    # so a user arriving from a state landing page (e.g. Oregon.html) lands
    # in a picker pre-focused on that state. picker.php / gauge_picker.php
    # both parse ?state= on the HTML entry — empty active_state means no
    # query string is appended.
    if picker_kind == "gauge":
        picker_href = "/gauge_picker.php"
        label = "Gauge<br>Picker"
    else:
        picker_href = "/picker.php"
        label = "Reach<br>Picker"
    if active_state:
        picker_href += f"?state={_urlquote(active_state)}"
    links.append(f'<a href="{picker_href}">{label}</a>')
    region = get_region_config()
    weather_url = region.weather_url_for(active_state)
    weather_label = (
        f"{_STATE_ABBREVS.get(active_state, '')}<br>Weather"
        if region.has_state_weather(active_state)
        else "Weather"
    )
    links.append(f'<a href="{weather_url}">{weather_label}</a>')
    return "\n    ".join(links)


def _build_right_cluster() -> str:
    """Right cluster on the header bar — just WKCC, desktop-only via CSS."""
    return (
        '<nav class="site-nav-right" aria-label="Account and external">'
        '<a href="https://wkcc.org" rel="noopener" target="_blank">WKCC</a>'
        "</nav>"
    )


def _build_footer_html() -> str:
    """Footer shared by all static pages.

    Login and Comment live here (only when EDITOR_FEATURE is on at build
    time) so the header can stay focused on navigation. Contact,
    Disclaimer, and Privacy Policy are always rendered.
    """
    items: list[str] = []
    if _editor_feature_on():
        items.append('<a href="/login.php">Login</a>')
        items.append('<a href="/comment.php">Comment</a>')
    items.append('<a href="/about.php">About</a>')
    items.append('<a href="/contact.php">Contact</a>')
    items.append('<a href="/disclaimer.php">Disclaimer</a>')
    items.append('<a href="/privacy.php">Privacy Policy</a>')
    links = " &middot; ".join(items)
    return (
        "<footer>\n"
        f"<p>{links}</p>\n"
        "<p>Data sourced from USGS, NOAA, USACE, USBR, "
        "and other government agencies.</p>\n"
        '<p>Code: <a href="/LICENSE.txt">GPL v3</a> '
        '&middot; Data: <a href="/LICENSE-DATA.txt">CC BY-NC 4.0</a></p>\n'
        "</footer>"
    )


def _build_letter_nav(letters: list[str]) -> str:
    """Build an A-Z letter navigation bar linking to #letter-X anchors."""
    if not letters:
        return ""
    links = " ".join(f'<a href="#letter-{ch}">{ch}</a>' for ch in letters)
    return f'<nav class="letter-nav" aria-label="Jump to river by letter">{links}</nav>'


def _build_page(
    table_html: str,
    css_link: str,
    states: list[str],
    current_state: str,
    title: str,
    letters: list[str] | None = None,
    filter_bar_html: str = "",
    active_page: str = "",
    picker_kind: str = "reach",
    path: str = "",
) -> str:
    """Wrap the table HTML in a complete HTML document linking to external CSS."""
    nav_html = _build_nav(
        states,
        active_state=current_state,
        active_page=active_page,
        picker_kind=picker_kind,
    )
    letter_nav_html = _build_letter_nav(letters) if letters else ""
    now_utc = datetime.now(UTC)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_display = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    desc = (
        f"Real-time river levels, flow, and gage data for {current_state} from USGS, NOAA, USACE, and other agencies."
        if current_state and current_state != "All States"
        else "Real-time river levels, flow, and gage data from USGS, NOAA, USACE, and other government agencies."
    )

    filter_tag = (
        f'<script src="/static/filters.js?v={_FILTERS_JS_VERSION}" defer></script>'
        if filter_bar_html
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
{_og_meta(title, desc, path)}
<meta name="theme-color" content="{BRAND_COLOR}" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="{BRAND_COLOR_DARK}" media="(prefers-color-scheme: dark)">
<link rel="icon" href="/static/favicon.ico">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<script src="/static/scroll-indicator.js" defer></script>
{css_link}
</head>
<body>
<a href="#main" class="skip-link">Skip to main content</a>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation" data-scroll-indicate>
    {nav_html}
  </nav>
  {_build_right_cluster()}
  {letter_nav_html}
</header>
<main id="main">
{filter_bar_html}
{table_html}
<div style="font-size:.75rem;color:var(--c-text-muted);margin-top:1rem;line-height:1.6">
<p><b>Status:</b>
<span class="level-low">Low</span> &ndash;
<span class="level-okay">Okay</span> &ndash;
<span class="level-high">High</span>
(thresholds set per reach based on flow or gage height)</p>
</div>
<p style="font-size:.7rem;color:var(--c-text-muted);margin-top:.5rem">Updated <time datetime="{now_iso}">{now_display}</time></p>
</main>
{_build_footer_html()}
{_LEVELS_JS}
{filter_tag}
</body>
</html>"""


def _build_placeholder_page(
    css_link: str,
    states: list[str],
    state: str,
) -> str:
    """Build the per-state landing page (e.g. Oregon.html, Montana.html).

    Renders cross-link anchors to the filtered all-states views and the
    pre-filtered pickers, then a curated external-resource list from the
    dataset region config (``kayak.dataset.region``). Reach-related anchors are
    suppressed when the state has no entry in ``states`` (the reach-states list
    from ``all_state_names()``) — e.g. a gauges-only state shows its gauge links
    but not the "Reaches in …" / "Reach picker — …" anchors until reaches land.
    """
    nav_html = _build_nav(states, active_state=state)
    state_qs = _urlquote(state)  # state in a URL query/fragment
    esc_state = html_mod.escape(state)  # state as HTML text (defense-in-depth)
    has_reaches = state in states
    cross_links: list[tuple[str, str]] = []
    if has_reaches:
        cross_links.append((f"Reaches in {esc_state}", f"/index.html#st={state_qs}"))
    cross_links.append((f"Live {esc_state} gauges", f"/gauges.html#st={state_qs}"))
    if has_reaches:
        cross_links.append((f"Reach picker — {esc_state}", f"/picker.php?state={state_qs}"))
    cross_links.append((f"Gauge picker — {esc_state}", f"/gauge_picker.php?state={state_qs}"))
    cross_link_items = "\n".join(
        f'<li><a href="{url}" '
        f'style="display:inline-flex;align-items:center;min-height:44px;font-weight:600">'
        f"→ {label}</a></li>"
        for label, url in cross_links
    )
    cross_links_html = (
        f'<ul style="margin:0 0 1.5em 0;padding-left:1.2em">\n{cross_link_items}\n</ul>'
    )

    links = get_region_config().links_for(state)
    link_items = "\n".join(
        f'<li><a href="{url}" style="display:inline-flex;align-items:center;min-height:44px">{label}</a></li>'
        for label, url in links
    )
    links_html = f"<ul>\n{link_items}\n</ul>" if links else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc_state} River Levels</title>
<meta name="description" content="Real-time river levels, flow, and gage data for {esc_state} from USGS, NOAA, USACE, and other agencies.">
{_og_meta(f"{esc_state} River Levels", f"Real-time river levels, flow, and gage data for {esc_state} from USGS, NOAA, USACE, and other agencies.", _state_page_path(state))}
<meta name="theme-color" content="{BRAND_COLOR}" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="{BRAND_COLOR_DARK}" media="(prefers-color-scheme: dark)">
<link rel="icon" href="/static/favicon.ico">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<script src="/static/scroll-indicator.js" defer></script>
{css_link}
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation" data-scroll-indicate>
    {nav_html}
  </nav>
  {_build_right_cluster()}
</header>
<main>
<h2>{esc_state}</h2>
{cross_links_html}
{links_html}
</main>
{_build_footer_html()}
</body>
</html>"""


def _build_map_page(
    css_link: str,
    states: list[str],
    geom_url: str,
    state_url: str,
    gauges_geom_url: str = "",
    gauges_state_url: str = "",
    site_config_url: str = "",
) -> str:
    """Build map.html with an interactive Leaflet map of all reaches.

    ``gauges_geom_url`` / ``gauges_state_url`` are Item 2's gauge-layer
    JSON URLs (map_and_ui_tweaks). When empty (e.g. in tests that don't
    build a gauge layer), the data attributes still render but
    static/map.js treats absent attrs as "no gauge layer to fetch".
    Defaulted for back-compat with the prior 4-arg signature.

    ``site_config_url`` is the generated ``site-config.json`` URL (S3d): the map's
    default extent + OSMB-style overlay layer defs. static/map.js fetches it and
    builds its layers + view from it (the layer GeoJSON URLs live inside that JSON,
    so the per-layer ``data-osmb-*-url`` attributes are gone).
    """
    nav_html = _build_nav(states, active_page="map")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>River Map</title>
<meta name="description" content="Interactive map of river reaches with real-time flow and level data.">
{_og_meta("River Map", "Interactive map of river reaches with real-time flow and level data.", "/map.html")}
<meta name="theme-color" content="{BRAND_COLOR}" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="{BRAND_COLOR_DARK}" media="(prefers-color-scheme: dark)">
<link rel="icon" href="/static/favicon.ico">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<script src="/static/scroll-indicator.js" defer></script>
<link rel="stylesheet" href="/static/leaflet.css">
{css_link}
<style>
#map {{height:calc(100vh - 5rem);width:100%;}}
main {{padding:0;max-width:none;}}
/* Unified Layers/Filters control. Pill visuals come from .filter-pills
   in style.css (shared with picker.php + state filter bars); only
   map-specific chrome lives here. Wrap is a flex column with stretch
   so the toggle hugs its content when collapsed and grows to panel
   width when expanded. */
.map-filter-wrap{{display:flex;flex-direction:column;background:var(--c-surface);border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);overflow:hidden;max-width:16rem}}
.map-filter-toggle{{display:flex;flex-direction:column;align-items:center;background:transparent;border:0;padding:6px 12px;font-size:.8rem;cursor:pointer;color:var(--c-text);line-height:1.2;font-family:inherit}}
.map-filter-toggle .mft-line{{display:block}}
.map-filter-toggle .mft-chevron{{font-size:.75rem;color:var(--c-text-muted);margin-top:2px;transition:transform .15s}}
.map-filter-toggle[aria-expanded="true"] .mft-chevron{{transform:rotate(180deg)}}
.map-filter{{display:none;border-top:1px solid var(--c-border-light);padding:8px 12px;font-size:.85rem;color:var(--c-text);max-height:calc(100dvh - 6rem);overflow-y:auto;scrollbar-width:thin;position:relative}}
.map-filter.is-open{{display:block}}
.map-filter fieldset{{border:0;padding:0;margin:0 0 .5rem}}
.map-filter legend{{font-weight:700;font-size:.7rem;text-transform:uppercase;letter-spacing:.02em;color:var(--c-text-muted);padding:0 0 3px}}
.map-filter .mf-stacked label{{display:flex;align-items:center;gap:6px;padding:2px 0;min-height:1.6rem;cursor:pointer}}
.map-filter .mf-stacked input{{margin:0;flex:0 0 auto}}
.map-filter .mf-count{{font-size:.75rem;color:var(--c-text-muted);padding-top:4px;border-top:1px solid var(--c-border-light);margin-top:.35rem}}
/* Sticky overflow indicator — added by JS when content exceeds panel
   height; iOS scrollbars stay hidden until touched, so this is the
   primary affordance. Native scrollbar still appears on top of this. */
.map-filter::after{{content:'▾';display:none;position:sticky;bottom:0;text-align:center;font-size:.85rem;line-height:1;color:var(--c-text-muted);background:linear-gradient(to top,var(--c-surface) 75%,rgba(0,0,0,0));padding:6px 0 3px;pointer-events:none;margin-top:-8px}}
.map-filter.has-overflow::after{{display:block}}
.map-error{{background:var(--c-surface);padding:8px 12px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);color:var(--c-low);font-size:.85rem;max-width:14rem}}
@media(max-width:640px){{.map-filter .mf-stacked label{{min-height:36px}}}}
/* Whole-popup link: zero leaflet's default content margin and move the
   spacing into the anchor's padding instead, so every visible pixel of
   the popup body is inside the <a> and tappable. */
.leaflet-popup-content{{margin:0}}
.reach-popup{{display:block;color:var(--c-text);text-decoration:none;padding:13px 20px;border-radius:12px;cursor:pointer}}
.reach-popup:hover{{background:var(--c-hover)}}
.reach-popup:focus-visible{{outline:2px solid var(--c-link);outline-offset:-2px;background:var(--c-hover)}}
.reach-popup .rp-name{{font-weight:700;font-size:.95rem;line-height:1.3}}
.reach-popup .rp-reading{{font-size:.85rem;margin-top:3px}}
.reach-popup .rp-trend{{color:var(--c-text-muted)}}
.reach-popup .rp-stale{{opacity:.55}}
.reach-popup .rp-footer{{display:flex;justify-content:space-between;align-items:baseline;gap:10px;font-size:.85rem;margin-top:3px}}
.reach-popup .rp-time{{color:var(--c-text-muted)}}
.reach-popup .rp-status-text{{text-transform:capitalize}}
.reach-popup .rp-tiers{{color:var(--c-text-muted)}}
.reach-popup .rp-dot{{font-size:1em;line-height:1}}
</style>
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation" data-scroll-indicate>
    {nav_html}
  </nav>
  {_build_right_cluster()}
</header>
<main>
<div id="map" data-geom-url="{html_mod.escape(geom_url, quote=True)}" data-state-url="{html_mod.escape(state_url, quote=True)}" data-gauges-geom-url="{html_mod.escape(gauges_geom_url, quote=True)}" data-gauges-state-url="{html_mod.escape(gauges_state_url, quote=True)}" data-site-config-url="{html_mod.escape(site_config_url, quote=True)}"></div>
</main>
{_build_footer_html()}
<script src="/static/leaflet.js" defer></script>
<script src="/static/map.js?v={_MAP_JS_VERSION}" defer></script>
</body>
</html>"""
