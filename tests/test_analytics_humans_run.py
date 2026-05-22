"""Integration-shaped tests that drive humans.run_* with synthetic events.

These exercise the full body of run_chunked / run_humans / run_paths /
run_countries / run_subdivisions (instead of monkey-patching them like
test_status_cli.py does for status-page rendering). The point is line
coverage: the per-function aggregation, filtering, sorting, and
markdown emission are all real code paths the operator depends on.

Real DNS, real Better Stack fetch, and the real DB-IP mmdb are all
stubbed so the tests stay hermetic and fast.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from zoneinfo import ZoneInfo

import pytest

from kayak.analytics import geoip, humans, monitors
from kayak.analytics._log_sources import AccessEvent

_TZ = ZoneInfo("America/Los_Angeles")
_REAL_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_BOT_UA = "Mozilla/5.0 (compatible; SomeBot/1.0; +http://example.com/bot)"


def _ev(
    ts: dt.datetime,
    client: str,
    path: str = "/",
    ua: str = _REAL_CHROME,
    status: int = 200,
    method: str = "GET",
) -> AccessEvent:
    return AccessEvent(
        ts=ts,
        client=client,
        method=method,
        path=path,
        status=status,
        bytes_sent=1024,
        ua=ua,
        rt=0.05,
        urt=None,
    )


def _make_events(now: dt.datetime) -> list[AccessEvent]:
    """A representative slice of 24h traffic for the run_* aggregations.

    Two humans (US + CA) hitting multiple pages + assets, two bot UAs, and
    one root-hammer that gets classified as 'root-only'.
    """
    base = now - dt.timedelta(hours=2)
    events: list[AccessEvent] = []
    # Human #1 (Comcast-style, US) browses /, /OR.html, /description.php,
    # plus a CSS asset.
    for i, path in enumerate(["/", "/OR.html", "/description.php?id=42", "/style.css"]):
        events.append(_ev(base + dt.timedelta(minutes=i), "203.0.113.5", path=path))
    # Human #2 (different network, CA-ish) browses gauge pages.
    for i, path in enumerate(["/", "/gauge.php?id=1", "/static/levels.js"]):
        events.append(_ev(base + dt.timedelta(minutes=10 + i), "203.0.113.6", path=path))
    # A real bot UA hitting the homepage a few times.
    for i in range(3):
        events.append(
            _ev(base + dt.timedelta(minutes=20 + i), "198.51.100.10", path="/", ua=_BOT_UA)
        )
    # A root-hammer: same IP, only `/`, many hits.
    for i in range(8):
        events.append(_ev(base + dt.timedelta(minutes=30 + i), "198.51.100.20", path="/"))
    # An asset-only ping (e.g. CDN warmup) — gets dropped from run_paths.
    events.append(_ev(base + dt.timedelta(minutes=40), "203.0.113.7", path="/static/favicon.ico"))
    return events


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the network/disk dependencies humans.run_* would otherwise touch."""

    now = dt.datetime.now(_TZ)
    events = _make_events(now)

    def _fake_iter(since: dt.datetime, log_glob: str = "") -> Iterable[AccessEvent]:
        # since-filter not enforced — the synthetic events are all recent.
        return iter(events)

    monkeypatch.setattr(humans, "iter_access_events", _fake_iter)
    # rDNS — keep main thread off the network. warm_rdns becomes a no-op.
    monkeypatch.setattr(humans, "warm_rdns", lambda _ips: None)
    monkeypatch.setattr(humans, "rdns", lambda _ip: "")
    # Better Stack fetch — no IPs in our test set, but the classifier still
    # calls is_betterstack().
    monkeypatch.setattr(monitors, "is_betterstack", lambda _ip: False)
    # Apple Private Relay would otherwise try to fetch Apple's CSV.
    from kayak.analytics import privacy_relays

    monkeypatch.setattr(privacy_relays, "is_apple_private_relay", lambda _ip: False)
    monkeypatch.setattr(privacy_relays, "apple_relay_region", lambda _ip: None)
    # GeoIP — return canned country / subdivision / ASN for the test IPs so
    # the run_countries / run_subdivisions / run_asns tables have something
    # to render. Shape: (cc, country_name, subdivision_name, asn, asn_org).
    fake = {
        "203.0.113.5": ("US", "United States", "Oregon", 7922, "Comcast Cable"),
        "203.0.113.6": ("CA", "Canada", "Ontario", 577, "Bell Canada"),
        "203.0.113.7": ("US", "United States", "Washington", 7922, "Comcast Cable"),
        "198.51.100.10": ("DE", "Germany", "", 24940, "Hetzner Online GmbH"),
        "198.51.100.20": ("AD", "Andorra", "", 48090, "TECHOFF SRV LIMITED"),
    }

    def _fake_lookup(ip, **_kw):
        return fake.get(ip, ("-", "", "", 0, ""))[0]

    def _fake_lookup_name(ip, **_kw):
        return fake.get(ip, ("-", "", "", 0, ""))[1]

    def _fake_lookup_subdivision(ip, **_kw):
        return fake.get(ip, ("-", "", "", 0, ""))[2]

    def _fake_lookup_asn(ip, **_kw):
        return fake.get(ip, ("-", "", "", 0, ""))[3]

    def _fake_lookup_asn_org(ip, **_kw):
        return fake.get(ip, ("-", "", "", 0, ""))[4]

    monkeypatch.setattr(geoip, "lookup", _fake_lookup)
    monkeypatch.setattr(geoip, "lookup_name", _fake_lookup_name)
    monkeypatch.setattr(geoip, "lookup_subdivision", _fake_lookup_subdivision)
    monkeypatch.setattr(geoip, "lookup_asn", _fake_lookup_asn)
    monkeypatch.setattr(geoip, "lookup_asn_org", _fake_lookup_asn_org)
    monkeypatch.setattr(geoip, "flush_cache", lambda: None)


def test_run_chunked_emits_markdown_table() -> None:
    out = humans.run_chunked(hours=24, bucket_hours=4, tz=_TZ)
    assert "Human / bot traffic" in out
    assert "| bucket start | humans | bots | other | distinct human IPs |" in out
    # The classifier should have caught the bot UA + the root-hammer.
    assert "Total bot hits" in out


def test_run_humans_includes_geo_and_per_ip_lines() -> None:
    out = humans.run_humans(hours=24, tz=_TZ)
    assert "# Distinct human visitors (24h)" in out
    # Country column populated for our human IPs.
    assert "| `203.0.113.5` | US |" in out
    assert "| `203.0.113.6` | CA |" in out
    # Org / AS column shows the per-IP ASN.
    assert "Comcast Cable (AS7922)" in out
    # Bot + no-assets IPs are filtered out.
    assert "198.51.100.10" not in out
    assert "198.51.100.20" not in out


def test_run_asns_groups_by_organization() -> None:
    out = humans.run_asns(hours=24, tz=_TZ)
    assert "# Hits by autonomous system" in out
    # The two synthetic humans (203.0.113.5 + 203.0.113.7) are both Comcast,
    # so they collapse into a single AS7922 row with their hits summed.
    assert "Comcast Cable (AS7922)" in out
    # Canada Bell only has one synthetic human.
    assert "Bell Canada (AS577)" in out


def test_run_paths_filters_assets_and_strips_query() -> None:
    out = humans.run_paths(hours=24, tz=_TZ)
    assert "# Hits by URL path" in out
    # /description.php?id=42 normalizes to /description.php.
    assert "| `/description.php` |" in out
    # Assets (style.css, favicon.ico, levels.js) get filtered.
    assert "style.css" not in out
    assert "favicon.ico" not in out
    assert "levels.js" not in out


def test_run_countries_groups_by_full_name() -> None:
    out = humans.run_countries(hours=24, tz=_TZ)
    assert "# Hits by country" in out
    # Full English name + ISO code shape.
    assert "United States (US)" in out
    assert "Canada (CA)" in out


def test_run_subdivisions_filters_to_us_ca() -> None:
    out = humans.run_subdivisions(hours=24, tz=_TZ)
    assert "Oregon (US)" in out
    assert "Ontario (CA)" in out
    # Andorra / Germany aren't in the wanted set.
    assert "Andorra" not in out
    assert "Germany" not in out


def test_run_subdivisions_custom_country_set() -> None:
    out = humans.run_subdivisions(hours=24, tz=_TZ, countries=("US",))
    # CA stays out when the wanted set is US-only.
    assert "Oregon (US)" in out
    assert "Ontario" not in out


def test_country_label_helper_handles_unknown() -> None:
    assert humans._country_label("US", "United States") == "United States (US)"
    assert humans._country_label("US", "") == "US"  # code-only when name missing
    assert humans._country_label("-", "") == "—"
    assert humans._country_label("", "") == "—"


def test_is_asset_path_known_extensions() -> None:
    assert humans._is_asset_path("/style.css")
    assert humans._is_asset_path("/static/levels.js")
    assert humans._is_asset_path("/static/reaches.geojson")
    assert humans._is_asset_path("/icon-180.png")
    assert humans._is_asset_path("/font.woff2")
    assert not humans._is_asset_path("/")
    assert not humans._is_asset_path("/description.php")
    assert not humans._is_asset_path("/OR.html")
    assert not humans._is_asset_path("/cgi/png")  # no extension, just a name
