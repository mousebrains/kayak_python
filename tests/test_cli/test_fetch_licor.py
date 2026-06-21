"""Tests for kayak.cli.fetch_licor (the POST-based LI-COR fetch step)."""

from __future__ import annotations

import argparse
import json

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session as SASession

from kayak.cli import fetch as fetch_mod
from kayak.cli import fetch_licor as licor_mod
from kayak.cli.fetch_licor import _LicorWork, _post, _prepare, build_request, fetch_licor
from kayak.db.models import Base, DataType, FetchUrl, Gauge, GaugeSource, Observation, Source
from kayak.parsers.registry import ensure_all_loaded

_FLOW_UUID = "flow-uuid-1111"
_LEVEL_UUID = "level-uuid-2222"
_TEMP_UUID = "temp-uuid-3333"
_AIR_UUID = "air-uuid-9999"
_DASH = "dash-abcdef"

_CONFIG_URL = (
    "https://www.licor.cloud/api/v2/timeseriesdata"
    f"?dashboardUUID={_DASH}"
    f"&flow={_FLOW_UUID}&gauge={_LEVEL_UUID}&temperature={_TEMP_UUID}"
    "&last=2&unit=days&interval=15&intervalUnit=minutes"
)

# 1700000000000 ms = 2023-11-14T22:13:20Z (safely in the past)
_TS_MS = 1700000000000

_ENDPOINT = "https://www.licor.cloud/api/v2/timeseriesdata"


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200, headers: dict | None = None):
        self.text = text
        self.status_code = status_code
        self.content = text.encode("utf-8")
        self.headers = headers or {}


def _sample_json(include_air: bool = True) -> str:
    """Three water channels + (optionally) an air-temperature channel to prove exclusion."""

    def rec(uuid, name, units, val):
        return {
            "channelUUID": uuid,
            "metricName": name,
            "metricUnits": units,
            "datum": {"valid": [[_TS_MS, val]], "error": []},
        }

    records = [
        rec(_FLOW_UUID, "Water Flow", "cfs", 305.0),
        rec(_LEVEL_UUID, "Water Level", "feet", 6.85),
        rec(_TEMP_UUID, "Water Temperature", "°F", 57.3),
    ]
    if include_air:
        records.append(rec(_AIR_UUID, "Air Temperature", "°F", 72.0))
    return json.dumps({"success": True, "value": {"records": records}})


def _work(url: str = _CONFIG_URL) -> _LicorWork:
    endpoint, body = build_request(url)
    return _LicorWork(url=url, endpoint=endpoint, body=body, source_map={}, source_id=1)


def _make_licor_source(
    session, url: str = _CONFIG_URL, n_sources: int = 1
) -> tuple[Source, FetchUrl]:
    fu = FetchUrl(url=url, parser="licor", is_active=True)
    session.add(fu)
    session.flush()
    first: Source | None = None
    for i in range(n_sources):
        src = Source(name=f"Kalama_LICOR_{i}", agency="LI-COR", fetch_url_id=fu.id)
        session.add(src)
        session.flush()
        gauge = Gauge(name=f"Kalama_{i}", usgs_id=None)
        session.add(gauge)
        session.flush()
        session.add(GaugeSource(gauge_id=gauge.id, source_id=src.id))
        session.flush()
        first = first or src
    assert first is not None
    return first, fu


# ---------------------------------------------------------------------------
# build_request — happy path + endpoint-rebuild invariant
# ---------------------------------------------------------------------------


def test_build_request_valid():
    endpoint, body = build_request(_CONFIG_URL)
    assert endpoint == _ENDPOINT
    assert body["dashboardUUID"] == _DASH
    assert body["time"] == {"relative": {"last": 2, "unit": "days"}}
    uuids = {c["channelUUID"] for c in body["channels"]}
    assert uuids == {_FLOW_UUID, _LEVEL_UUID, _TEMP_UUID}
    flow_channel = next(c for c in body["channels"] if c["channelUUID"] == _FLOW_UUID)
    assert flow_channel["metricName"] == "com.onset.sensordata.waterflow_us"
    assert flow_channel["aggregationFunction"] == "avg"
    assert flow_channel["aggregationInterval"] == {"value": 15, "unit": "minutes"}


@pytest.mark.parametrize(
    "host_url",
    [
        # userinfo decoy: hostname is the real licor host, userinfo is dropped
        "https://evil.com@www.licor.cloud/api/v2/timeseriesdata"
        f"?dashboardUUID={_DASH}&flow={_FLOW_UUID}&gauge={_LEVEL_UUID}&temperature={_TEMP_UUID}",
        # explicit port: stripped by the scheme://hostname/path rebuild
        "https://www.licor.cloud:8080/api/v2/timeseriesdata"
        f"?dashboardUUID={_DASH}&flow={_FLOW_UUID}&gauge={_LEVEL_UUID}&temperature={_TEMP_UUID}",
        # uppercase host: urlparse lowercases hostname
        "https://WWW.LICOR.CLOUD/api/v2/timeseriesdata"
        f"?dashboardUUID={_DASH}&flow={_FLOW_UUID}&gauge={_LEVEL_UUID}&temperature={_TEMP_UUID}",
    ],
)
def test_build_request_rebuilds_clean_endpoint(host_url):
    """The endpoint is reconstructed from hostname+path — userinfo/port/case can't leak."""
    endpoint, _ = build_request(host_url)
    assert endpoint == _ENDPOINT


@pytest.mark.parametrize(
    "url",
    [
        # host bypass via userinfo (hostname is actually evil.com)
        f"https://www.licor.cloud@evil.com/api/v2/timeseriesdata?dashboardUUID={_DASH}&flow={_FLOW_UUID}&gauge={_LEVEL_UUID}&temperature={_TEMP_UUID}",
        "https://evil.example.com/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c",
        "https://www.licor.cloud./api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c",  # trailing dot
        "https://www.licor.cloud/api/v2/OTHER?dashboardUUID=x&flow=a&gauge=b&temperature=c",
        "ftp://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c",
        "http://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c",  # http rejected (https only)
        "https://www.licor.cloud/api/v2/timeseriesdata?flow=a&gauge=b&temperature=c",  # no dashboard
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&gauge=b&temperature=c",  # no flow
        # duplicate channel UUID across params (would mis-type one series)
        f"https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow={_FLOW_UUID}&gauge={_FLOW_UUID}&temperature=c",
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&last=99",
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&last=abc",
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&unit=years",
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&interval=0",
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&interval=abc",
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&intervalUnit=seconds",
    ],
)
def test_build_request_rejects_bad_url(url):
    with pytest.raises(ValueError):
        build_request(url)


# ---------------------------------------------------------------------------
# _prepare — selection + config-error counting
# ---------------------------------------------------------------------------


def test_prepare_selects_only_post_parsers(session):
    ensure_all_loaded()
    _make_licor_source(session)
    get_fu = FetchUrl(url="https://example.com/data", parser="nwps", is_active=True)
    session.add(get_fu)
    session.flush()
    session.add(Source(name="get_source", agency="USGS", fetch_url_id=get_fu.id))
    session.flush()

    work, config_errors = _prepare(session, ignore_constraints=False)
    assert config_errors == 0
    assert len(work) == 1
    assert work[0].url == _CONFIG_URL
    assert work[0].source_id is not None
    assert work[0].endpoint == _ENDPOINT


def test_prepare_counts_bad_config_url(session):
    ensure_all_loaded()
    bad = (
        "https://www.licor.cloud/api/v2/timeseriesdata?flow=a&gauge=b&temperature=c"  # no dashboard
    )
    _make_licor_source(session, url=bad)
    work, config_errors = _prepare(session, ignore_constraints=False)
    assert work == []
    assert config_errors == 1


def test_prepare_rejects_multi_source_row(session):
    ensure_all_loaded()
    _make_licor_source(session, n_sources=2)
    work, config_errors = _prepare(session, ignore_constraints=False)
    assert work == []  # not silently dropped at parse time — refused up front
    assert config_errors == 1


def test_prepare_honors_hour_constraint(session, monkeypatch):
    """A row throttled to other hours is skipped (not POSTed every run); the
    --ignore-constraints override bypasses the gate.

    `_hour_allowed` is monkeypatched (not driven by the wall clock) so the test
    is deterministic across a UTC hour rollover.
    """
    ensure_all_loaded()
    _src, fu = _make_licor_source(session)
    fu.hours = "3"  # value is irrelevant; the gate result is stubbed below
    session.flush()

    monkeypatch.setattr(licor_mod, "_hour_allowed", lambda spec: False)  # outside window
    assert _prepare(session, ignore_constraints=False) == ([], 0)  # skipped, no fetch
    work, config_errors = _prepare(session, ignore_constraints=True)  # override bypasses the gate
    assert len(work) == 1
    assert config_errors == 0


# ---------------------------------------------------------------------------
# default `fetch` skips POST parsers (positive + negative in one call)
# ---------------------------------------------------------------------------


def _fetch_args(**over):
    base = dict(
        parser_filter=None,
        url_filter=None,
        ignore_constraints=True,
        fetch_only=False,
        url_prefix="",
        parser_type=None,
        show_name=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_default_fetch_skips_licor_but_keeps_get(session):
    ensure_all_loaded()
    _make_licor_source(session)
    get_fu = FetchUrl(url="https://example.com/data", parser="nwps", is_active=True)
    session.add(get_fu)
    session.flush()
    session.add(Source(name="get_source", agency="USGS", fetch_url_id=get_fu.id))
    session.flush()

    work_items = fetch_mod._prepare_work_items(session, _fetch_args())
    urls = [w.url for w in work_items]
    assert urls == ["https://example.com/data"]  # GET kept, licor dropped


def test_default_fetch_skips_licor_even_in_fetch_only(session):
    ensure_all_loaded()
    _make_licor_source(session)
    work_items = fetch_mod._prepare_work_items(session, _fetch_args(fetch_only=True))
    assert work_items == []


# ---------------------------------------------------------------------------
# _fetch_one — SSRF guard + fail-closed
# ---------------------------------------------------------------------------


def test_fetch_one_ssrf_rejection_blocks_post(monkeypatch):
    """The real _validate_url branch: a blocked endpoint returns None, no POST."""

    def _boom(*a, **k):
        raise AssertionError("requests.post must not run when _validate_url rejects")

    monkeypatch.setattr(
        licor_mod, "_validate_url", lambda url: (_ for _ in ()).throw(ValueError("blocked IP"))
    )
    monkeypatch.setattr(licor_mod.requests, "post", _boom)
    assert licor_mod._fetch_one(_work()) is None


def test_fetch_one_posts_with_redirects_disabled(monkeypatch):
    captured = {}

    def fake_post(endpoint, json=None, headers=None, timeout=None, allow_redirects=None):
        captured["endpoint"] = endpoint
        captured["body"] = json
        captured["headers"] = headers
        captured["allow_redirects"] = allow_redirects
        return _FakeResp(_sample_json())

    monkeypatch.setattr(licor_mod, "_validate_url", lambda url: None)
    monkeypatch.setattr(licor_mod.requests, "post", fake_post)

    text = licor_mod._fetch_one(_work())
    assert text == _sample_json()
    assert captured["endpoint"] == _ENDPOINT
    assert captured["body"]["dashboardUUID"] == _DASH
    assert captured["allow_redirects"] is False  # SSRF: redirects must not be followed
    # Identify as the configured pipeline UA, not python-requests.
    assert captured["headers"]["User-Agent"] == licor_mod.FETCH_USER_AGENT


# ---------------------------------------------------------------------------
# _post — status / redirect / retry / cap / exception
# ---------------------------------------------------------------------------


def test_post_treats_3xx_as_failure(monkeypatch):
    monkeypatch.setattr(licor_mod.requests, "post", lambda *a, **k: _FakeResp("", 302))
    assert _post(_ENDPOINT, {}, 10) is None


def test_post_http_error_returns_none(monkeypatch):
    monkeypatch.setattr(licor_mod.requests, "post", lambda *a, **k: _FakeResp("nope", 500))
    assert _post(_ENDPOINT, {}, 10) is None


def test_post_retries_on_429_then_succeeds(monkeypatch):
    calls = {"n": 0}
    sleeps: list[int] = []

    def fake_post(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            return _FakeResp("", 429)
        return _FakeResp(_sample_json())

    monkeypatch.setattr(licor_mod.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(licor_mod.requests, "post", fake_post)
    assert _post(_ENDPOINT, {}, 10) == _sample_json()
    assert sleeps == [1, 2]  # 2**0, 2**1


def test_post_gives_up_after_persistent_429(monkeypatch):
    monkeypatch.setattr(licor_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(licor_mod.requests, "post", lambda *a, **k: _FakeResp("", 429))
    assert _post(_ENDPOINT, {}, 10) is None


def test_post_request_exception_returns_none(monkeypatch):
    def raiser(*a, **k):
        raise licor_mod.requests.RequestException("network down")

    monkeypatch.setattr(licor_mod.requests, "post", raiser)
    assert _post(_ENDPOINT, {}, 10) is None


def test_post_rejects_oversized_content_length(monkeypatch):
    big = str(licor_mod._MAX_BODY_BYTES + 1)
    monkeypatch.setattr(
        licor_mod.requests, "post", lambda *a, **k: _FakeResp("x", headers={"Content-Length": big})
    )
    assert _post(_ENDPOINT, {}, 10) is None


# ---------------------------------------------------------------------------
# fetch_licor — end to end (real cross-session persistence) + variants
# ---------------------------------------------------------------------------


def _file_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'kayak.db'}")

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return engine


def test_fetch_licor_persists_across_sessions(tmp_path, monkeypatch):
    """Real two-phase session lifecycle: commit must survive into a NEW session."""
    ensure_all_loaded()
    engine = _file_engine(tmp_path)
    seed = SASession(engine)
    src, _ = _make_licor_source(seed)
    seed.commit()
    src_id = src.id
    seed.close()

    monkeypatch.setattr(licor_mod, "get_session", lambda: SASession(engine))
    monkeypatch.setattr(licor_mod, "_validate_url", lambda url: None)
    monkeypatch.setattr(licor_mod.requests, "post", lambda *a, **k: _FakeResp(_sample_json()))

    rc = fetch_licor(argparse.Namespace(dry_run=False, show_name=False))
    assert rc == 0

    check = SASession(engine)  # brand-new session — proves the commit persisted
    obs = check.scalars(select(Observation).where(Observation.source_id == src_id)).all()
    by_type = {o.data_type: o.value for o in obs}
    # air temperature excluded; only the three water channels stored
    assert by_type == {
        DataType.flow: pytest.approx(305.0),
        DataType.gauge: pytest.approx(6.85),
        DataType.temperature: pytest.approx(57.3),
    }
    check.close()
    engine.dispose()


def test_fetch_licor_dry_run_writes_nothing(tmp_path, monkeypatch):
    ensure_all_loaded()
    engine = _file_engine(tmp_path)
    seed = SASession(engine)
    src, _ = _make_licor_source(seed)
    seed.commit()
    src_id = src.id
    seed.close()

    monkeypatch.setattr(licor_mod, "get_session", lambda: SASession(engine))
    monkeypatch.setattr(licor_mod, "_validate_url", lambda url: None)
    monkeypatch.setattr(licor_mod.requests, "post", lambda *a, **k: _FakeResp(_sample_json()))

    rc = fetch_licor(argparse.Namespace(dry_run=True, show_name=False))
    assert rc == 0
    check = SASession(engine)
    assert check.scalars(select(Observation).where(Observation.source_id == src_id)).all() == []
    check.close()
    engine.dispose()


def test_fetch_licor_bad_config_alerts_and_makes_no_request(tmp_path, monkeypatch):
    """A malformed dataset URL: returns 1 (alert), stores nothing, never POSTs."""
    ensure_all_loaded()
    engine = _file_engine(tmp_path)
    seed = SASession(engine)
    bad = (
        "https://www.licor.cloud/api/v2/timeseriesdata?flow=a&gauge=b&temperature=c"  # no dashboard
    )
    src, _ = _make_licor_source(seed, url=bad)
    seed.commit()
    src_id = src.id
    seed.close()

    def _boom(*a, **k):
        raise AssertionError("no POST should happen for a fail-closed config")

    monkeypatch.setattr(licor_mod, "get_session", lambda: SASession(engine))
    monkeypatch.setattr(licor_mod.requests, "post", _boom)

    rc = fetch_licor(argparse.Namespace(dry_run=False, show_name=False))
    assert rc == 1  # config error surfaces in the exit code (soft-fail alert)
    check = SASession(engine)
    assert check.scalars(select(Observation).where(Observation.source_id == src_id)).all() == []
    check.close()
    engine.dispose()
