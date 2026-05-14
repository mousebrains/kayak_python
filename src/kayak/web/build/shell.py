"""HTML page-shell helpers: nav, footer, letter-nav, page wrappers."""

import html as html_mod
from datetime import UTC, datetime

from kayak.web.build._shared import (
    _FILTERS_JS_VERSION,
    _LEVELS_JS,
    _MAP_JS_VERSION,
    _NAV_STATES,
    _STATE_ABBREVS,
    BRAND_COLOR,
    BRAND_COLOR_DARK,
    _editor_feature_on,
    _og_meta,
)

# Windy.com center coords for the "Weather" nav link, per active state.
# These mirror the per-state external-resource URLs further down in this
# file (the {State} Weather — Windy entries in _STATE_LINKS). Pages with
# no active state (all-reaches index, map, gauges) fall back to a PNW
# overview view; the user can pan from there.
_STATE_WEATHER_URL: dict[str, str] = {
    "Oregon": "https://www.windy.com/?44.0,-120.5,7",
    "Washington": "https://www.windy.com/?47.5,-120.5,7",
    "Idaho": "https://www.windy.com/?44.4,-114.7,7",
    "Nevada": "https://www.windy.com/?39.5,-116.9,7",
    "California": "https://www.windy.com/?37.2,-119.5,6",
}
_DEFAULT_WEATHER_URL = "https://www.windy.com/?43.0,-118.0,6"

# Links for adjacent state pages
_STATE_LINKS: dict[str, list[tuple[str, str]]] = {
    "Oregon": [
        (
            "American Whitewater — Oregon",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-ORE",
        ),
        (
            "Dreamflows — Oregon Coastal",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Coastal_Rivers",
        ),
        (
            "Dreamflows — Oregon Central",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Central_Rivers",
        ),
        (
            "Dreamflows — Oregon Eastern",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Oregon_Eastern_Rivers",
        ),
        ("Oregon Kayaking", "https://oregonkayaking.net"),
        ("USGS Oregon Water Data", "https://waterdata.usgs.gov/state/oregon/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Willamette Kayak and Canoe Club", "https://wkcc.org"),
        ("Oregon Whitewater Association", "https://oregonwhitewater.org"),
        ("Oregon Weather — Windy", "https://www.windy.com/?44.0,-120.5,7"),
    ],
    "Washington": [
        (
            "American Whitewater — Washington",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-WSH",
        ),
        (
            "Dreamflows — Washington",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Washington_Rivers",
        ),
        ("USGS Washington Water Data", "https://waterdata.usgs.gov/state/washington/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Professor Paddle", "https://www.professorpaddle.com"),
        ("Washington Weather — Windy", "https://www.windy.com/?47.5,-120.5,7"),
        ("Washington Kayak Club", "http://wakayakclub.clubexpress.com"),
    ],
    "Idaho": [
        (
            "American Whitewater — Idaho",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-IDA",
        ),
        (
            "Dreamflows — Idaho",
            "https://www.dreamflows.com/flows.php?zone=panw&page=prod&form=norm&mark=All#Idaho_Rivers",
        ),
        ("USGS Idaho Water Data", "https://waterdata.usgs.gov/state/idaho/"),
        ("NW River Forecast Center", "https://www.nwrfc.noaa.gov/rfc/"),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Idaho Rivers United", "https://www.idahorivers.org"),
        ("Idaho Whitewater Association", "https://idahowhitewater.org"),
        ("Idaho Dept. of Water Resources", "https://idwr.idaho.gov"),
        ("Idaho Weather — Windy", "https://www.windy.com/?44.4,-114.7,7"),
    ],
    "Nevada": [
        ("USGS Nevada Water Data", "https://waterdata.usgs.gov/state/nevada/"),
        ("Colorado Basin River Forecast Center", "https://www.cbrfc.noaa.gov"),
        (
            "American Whitewater — Nevada",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-NEV",
        ),
        ("USBR Hydromet", "https://www.usbr.gov/pn/hydromet/datamenu.html"),
        ("Nevada Weather — Windy", "https://www.windy.com/?39.5,-116.9,7"),
    ],
    "California": [
        ("Dreamflows", "https://www.dreamflows.com"),
        (
            "American Whitewater — California",
            "https://www.americanwhitewater.org/content/River/view/river-index/state/USA-CAL",
        ),
        ("USGS California Water Data", "https://waterdata.usgs.gov/state/california/"),
        ("California Nevada River Forecast Center", "https://www.cnrfc.noaa.gov"),
        ("California Creeks", "https://cacreeks.com"),
        ("Gold Country Paddlers", "https://goldcountrypaddlers.org"),
        ("California Weather — Windy", "https://www.windy.com/?37.2,-119.5,6"),
    ],
}


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
    for s in states:
        if s not in _NAV_STATES:
            continue
        abbrev = _STATE_ABBREVS.get(s, s)
        cls = ' class="active"' if s == active_state else ""
        links.append(f'<a href="/{s}.html"{cls}>{abbrev}</a>')
    if picker_kind == "gauge":
        links.append('<a href="/gauge_picker.php">Gauge<br>Picker</a>')
    else:
        links.append('<a href="/picker.php">Reach<br>Picker</a>')
    weather_url = _STATE_WEATHER_URL.get(active_state, _DEFAULT_WEATHER_URL)
    weather_label = (
        f"{_STATE_ABBREVS.get(active_state, '')}<br>Weather"
        if active_state in _STATE_WEATHER_URL
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
{css_link}
</head>
<body>
<a href="#main" class="skip-link">Skip to main content</a>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation">
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


def _build_placeholder_page(css_link: str, states: list[str], state: str) -> str:
    """Build a links page for a non-primary state."""
    nav_html = _build_nav(states, active_state=state)
    links = _STATE_LINKS.get(state, [])
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
<title>{state} River Levels</title>
<meta name="description" content="Real-time river levels, flow, and gage data for {state} from USGS, NOAA, USACE, and other agencies.">
{_og_meta(f"{state} River Levels", f"Real-time river levels, flow, and gage data for {state} from USGS, NOAA, USACE, and other agencies.", f"/{state}.html")}
<meta name="theme-color" content="{BRAND_COLOR}" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="{BRAND_COLOR_DARK}" media="(prefers-color-scheme: dark)">
<link rel="icon" href="/static/favicon.ico">
<link rel="manifest" href="/static/manifest.json">
<link rel="apple-touch-icon" href="/static/icon-180.png">
{css_link}
</head>
<body>
<header>
  <h1><a href="/index.html">River Levels</a></h1>
  <nav aria-label="State navigation">
    {nav_html}
  </nav>
  {_build_right_cluster()}
</header>
<main>
<h2>{state}</h2>
{links_html}
</main>
{_build_footer_html()}
</body>
</html>"""


def _build_map_page(css_link: str, states: list[str], geom_url: str, state_url: str) -> str:
    """Build map.html with an interactive Leaflet map of all reaches."""
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
<link rel="stylesheet" href="/static/leaflet.css">
{css_link}
<style>
#map {{height:calc(100vh - 5rem);width:100%;}}
main {{padding:0;max-width:none;}}
.map-filter{{background:var(--c-surface);padding:6px 10px;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);font-size:.85rem;color:var(--c-text);max-width:13rem}}
.map-filter fieldset{{border:0;padding:0;margin:0 0 .35rem}}
.map-filter legend{{font-weight:700;font-size:.75rem;text-transform:uppercase;letter-spacing:.02em;color:var(--c-text-muted);padding:0 0 2px}}
.map-filter label{{display:flex;align-items:center;gap:6px;padding:2px 0;min-height:1.6rem;cursor:pointer}}
.map-filter input[type=checkbox]{{margin:0;flex:0 0 auto}}
.map-filter .swatch{{display:inline-block;width:10px;height:10px;border-radius:2px;border:1px solid rgba(0,0,0,.15)}}
.map-filter .mf-count{{font-size:.75rem;color:var(--c-text-muted);padding-top:2px;border-top:1px solid var(--c-border-light);margin-top:.35rem}}
.map-filter .mf-err{{color:var(--c-low);font-size:.75rem}}
.map-filter-toggle{{display:none;background:var(--c-surface);padding:6px 10px;border:0;border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.3);font-size:.85rem;cursor:pointer}}
@media(max-width:640px){{
  .map-filter-toggle{{display:block}}
  .map-filter{{display:none}}
  .map-filter.is-open{{display:block}}
  .map-filter label{{min-height:44px}}
}}
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
  <nav aria-label="State navigation">
    {nav_html}
  </nav>
  {_build_right_cluster()}
</header>
<main>
<div id="map" data-geom-url="{html_mod.escape(geom_url, quote=True)}" data-state-url="{html_mod.escape(state_url, quote=True)}"></div>
</main>
{_build_footer_html()}
<script src="/static/leaflet.js" defer></script>
<script src="/static/map.js?v={_MAP_JS_VERSION}" defer></script>
</body>
</html>"""
