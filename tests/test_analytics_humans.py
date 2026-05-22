"""Tests for the human-vs-bot classifier in kayak.analytics.humans."""

from __future__ import annotations

import collections

import pytest

from kayak.analytics import humans


@pytest.fixture(autouse=True)
def _no_external_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the classifier off the network during tests.

    Three escape hatches:
    - ``rdns`` would block on real DNS for test-net IPs (~10s/IP NXDOMAIN).
    - ``monitors.is_betterstack`` would fetch the IP list over HTTPS.
    - The geoip module wraps a maxminddb reader; not called from the unit
      test cases here, but stubbed for belt-and-suspenders.
    """
    monkeypatch.setattr(humans, "rdns", lambda _ip: "")
    monkeypatch.setattr(humans.monitors, "is_betterstack", lambda _ip: False)
    monkeypatch.setattr(humans.geoip, "lookup", lambda ip, **_kw: "-")


def _paths(items: dict[str, int] | None = None) -> collections.Counter[str]:
    return collections.Counter(items or {})


_REAL_SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
_REAL_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
# Same shape as 2a09:bac2:* / 45.148.10.205 in production logs — truncated UA
# that *would* fool a "no Safari token = bot" heuristic. Privacy-mode browsers
# emit similar shapes (iCloud Private Relay, Firefox RFP), so we don't classify
# by UA shape alone any more.
_TRUNCATED_SAFARI = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"
)


def test_real_browser_hitting_assets_is_human() -> None:
    paths = _paths({"/": 1, "/style.css": 1, "/static/levels.js": 1})
    cls = humans._classify_ip("203.0.113.5", _REAL_SAFARI, set(paths), paths_counter=paths, hits=3)
    assert cls == "human"


def test_root_hammer_flagged_as_no_assets() -> None:
    paths = _paths({"/": 1650})
    cls = humans._classify_ip(
        "2a09:bac2:b089:119::1c:339",
        _TRUNCATED_SAFARI,
        set(paths),
        paths_counter=paths,
        hits=1650,
    )
    assert cls == "no-assets"


def test_post_hammer_flagged_as_no_assets() -> None:
    # 45.148.10.205 in production: 361 POSTs to / with a truncated Chrome UA.
    # We don't track method explicitly, but the path-set still gives the signal.
    paths = _paths({"/": 361})
    cls = humans._classify_ip(
        "45.148.10.205", _REAL_CHROME, set(paths), paths_counter=paths, hits=361
    )
    assert cls == "no-assets"


def test_alibaba_singapore_botnet_flagged_as_no_assets() -> None:
    # The SG botnet pattern: hit homepage + sparklines.json a couple times
    # using a random recent Chrome UA, never load .css / .js. Sparklines is
    # an asset extension (.json) but not browser-mandatory — scrapers fetch
    # it directly for the data without ever pulling levels.js / style.css.
    paths = _paths({"/": 1, "/static/sparklines.json": 1})
    cls = humans._classify_ip("47.82.10.78", _REAL_CHROME, set(paths), paths_counter=paths, hits=2)
    assert cls == "no-assets"


def test_gauge_only_no_browser_flagged_as_no_assets() -> None:
    # Bot hitting only /gauge.php a few times, no JS / CSS — bot-shaped.
    paths = _paths({"/gauge.php": 3})
    cls = humans._classify_ip("47.82.10.80", _REAL_CHROME, set(paths), paths_counter=paths, hits=3)
    assert cls == "no-assets"


def test_one_hit_to_root_is_not_no_assets() -> None:
    # Threshold is _NO_ASSETS_MIN_HITS=2: a single-hit visitor could be a
    # one-page bouncer (real user reading then leaving) — we can't tell
    # the difference from one hit alone, so leave it as human.
    paths = _paths({"/": 1})
    cls = humans._classify_ip("203.0.113.10", _REAL_SAFARI, set(paths), paths_counter=paths, hits=1)
    assert cls == "human"


def test_paths_with_js_kept_as_human() -> None:
    # A real browser fetches at least one .js — even with everything else
    # cached, gauge.php / description.php pull in feature-map.js / etc.
    paths = _paths({"/": 1, "/static/levels.js": 1})
    cls = humans._classify_ip("203.0.113.20", _REAL_CHROME, set(paths), paths_counter=paths, hits=2)
    assert cls == "human"


def test_paths_with_css_kept_as_human() -> None:
    # Symmetric to the .js case — any .css hit is enough to convince the
    # classifier a real browser is on the other end.
    paths = _paths({"/": 1, "/static/style-8680f4b53a.css": 1})
    cls = humans._classify_ip("203.0.113.21", _REAL_SAFARI, set(paths), paths_counter=paths, hits=2)
    assert cls == "human"


def test_truncated_safari_with_asset_hits_is_human() -> None:
    # The privacy-mode case: a browser sending a stripped UA that hits assets
    # is treated as human (the whole point of backing out the UA heuristic).
    paths = _paths({"/": 1, "/style.css": 1})
    cls = humans._classify_ip(
        "2a09:bac2:b089:119::1c:339",
        _TRUNCATED_SAFARI,
        set(paths),
        paths_counter=paths,
        hits=2,
    )
    assert cls == "human"


def test_known_bot_ua_still_bot_even_if_no_assets() -> None:
    # Bot UAs are caught by _BOT_RE before _is_no_browser_assets can fire.
    paths = _paths({"/": 50})
    cls = humans._classify_ip(
        "203.0.113.42",
        "Mozilla/5.0 (compatible; UptimeBot/1.0)",
        set(paths),
        paths_counter=paths,
        hits=50,
    )
    assert cls == "bot"


def test_classifier_callable_without_paths_counter() -> None:
    # run_chunked used to call _classify_ip(ip, ua, paths) without keyword args.
    # The new signature must stay backward-compatible.
    cls = humans._classify_ip("203.0.113.5", _REAL_SAFARI, {"/", "/style.css"})
    assert cls == "human"
