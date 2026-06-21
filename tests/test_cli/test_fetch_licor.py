"""Tests for kayak.cli.fetch_licor (the POST-based LI-COR fetch step)."""

from __future__ import annotations

import argparse
import json

import pytest
from sqlalchemy import select

from kayak.cli import fetch as fetch_mod
from kayak.cli import fetch_licor as licor_mod
from kayak.cli.fetch_licor import _prepare, build_request, fetch_licor
from kayak.db.models import DataType, FetchUrl, Gauge, GaugeSource, Observation, Source
from kayak.parsers.registry import ensure_all_loaded

_FLOW_UUID = "flow-uuid-1111"
_LEVEL_UUID = "level-uuid-2222"
_TEMP_UUID = "temp-uuid-3333"
_DASH = "dash-abcdef"

_CONFIG_URL = (
    "https://www.licor.cloud/api/v2/timeseriesdata"
    f"?dashboardUUID={_DASH}"
    f"&flow={_FLOW_UUID}&gauge={_LEVEL_UUID}&temperature={_TEMP_UUID}"
    "&last=2&unit=days&interval=15&intervalUnit=minutes"
)

# 1700000000000 ms = 2023-11-14T22:13:20Z (safely in the past)
_TS_MS = 1700000000000


class _FakeResp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.content = text.encode("utf-8")


def _sample_json() -> str:
    def rec(uuid, name, units, val):
        return {
            "channelUUID": uuid,
            "metricName": name,
            "metricUnits": units,
            "datum": {"valid": [[_TS_MS, val]], "error": []},
        }

    return json.dumps(
        {
            "success": True,
            "value": {
                "records": [
                    rec(_FLOW_UUID, "Water Flow", "cfs", 305.0),
                    rec(_LEVEL_UUID, "Water Level", "feet", 6.85),
                    rec(_TEMP_UUID, "Water Temperature", "°F", 57.3),
                ]
            },
        }
    )


def _make_licor_source(session) -> tuple[Source, FetchUrl]:
    fu = FetchUrl(url=_CONFIG_URL, parser="licor", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name="Kalama_ItalianCreek_LICOR", agency="LI-COR", fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    gauge = Gauge(name="Kalama_ItalianCreek", usgs_id=None)
    session.add(gauge)
    session.flush()
    session.add(GaugeSource(gauge_id=gauge.id, source_id=src.id))
    session.flush()
    return src, fu


# ---------------------------------------------------------------------------
# build_request
# ---------------------------------------------------------------------------


def test_build_request_valid():
    endpoint, body = build_request(_CONFIG_URL)
    assert endpoint == "https://www.licor.cloud/api/v2/timeseriesdata"
    assert body["dashboardUUID"] == _DASH
    assert body["time"] == {"relative": {"last": 2, "unit": "days"}}
    uuids = {c["channelUUID"] for c in body["channels"]}
    assert uuids == {_FLOW_UUID, _LEVEL_UUID, _TEMP_UUID}
    flow_channel = next(c for c in body["channels"] if c["channelUUID"] == _FLOW_UUID)
    assert flow_channel["metricName"] == "com.onset.sensordata.waterflow_us"
    assert flow_channel["aggregationFunction"] == "avg"
    assert flow_channel["aggregationInterval"] == {"value": 15, "unit": "minutes"}


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.com/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c",
        "https://www.licor.cloud/api/v2/OTHER?dashboardUUID=x&flow=a&gauge=b&temperature=c",
        "ftp://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c",
        "https://www.licor.cloud/api/v2/timeseriesdata?flow=a&gauge=b&temperature=c",  # no dashboard
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&gauge=b&temperature=c",  # no flow
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&last=99",
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&last=abc",
        "https://www.licor.cloud/api/v2/timeseriesdata?dashboardUUID=x&flow=a&gauge=b&temperature=c&unit=years",
    ],
)
def test_build_request_rejects_bad_url(url):
    with pytest.raises(ValueError):
        build_request(url)


# ---------------------------------------------------------------------------
# _prepare — selection by POST transport
# ---------------------------------------------------------------------------


def test_prepare_selects_only_post_parsers(session):
    ensure_all_loaded()
    _make_licor_source(session)
    # A GET parser row that must NOT be picked up by fetch-licor.
    get_fu = FetchUrl(url="https://example.com/data", parser="nwps", is_active=True)
    session.add(get_fu)
    session.flush()
    session.add(Source(name="get_source", agency="USGS", fetch_url_id=get_fu.id))
    session.flush()

    work = _prepare(session)
    assert len(work) == 1
    assert work[0].url == _CONFIG_URL
    assert work[0].source_id is not None  # single-source → lone-source attribution


# ---------------------------------------------------------------------------
# default `fetch` skips POST parsers
# ---------------------------------------------------------------------------


def test_default_fetch_skips_post_parser(session):
    ensure_all_loaded()
    _make_licor_source(session)
    args = argparse.Namespace(
        parser_filter=None,
        url_filter=None,
        ignore_constraints=True,
        fetch_only=False,
        url_prefix="",
        parser_type=None,
        show_name=False,
    )
    work_items = fetch_mod._prepare_work_items(session, args)
    assert work_items == []  # the lone licor row is skipped (POST transport)


# ---------------------------------------------------------------------------
# _fetch_one — fail-closed + POST
# ---------------------------------------------------------------------------


def test_fetch_one_bad_url_fails_closed_without_network(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("requests.post must not be called for a bad config URL")

    monkeypatch.setattr(licor_mod.requests, "post", _boom)
    assert licor_mod._fetch_one("https://evil.example.com/x") is None


def test_fetch_one_posts_and_returns_text(monkeypatch):
    captured = {}

    def fake_post(endpoint, json=None, headers=None, timeout=None):
        captured["endpoint"] = endpoint
        captured["body"] = json
        return _FakeResp(_sample_json())

    monkeypatch.setattr(licor_mod, "_validate_url", lambda url: None)  # avoid real DNS
    monkeypatch.setattr(licor_mod.requests, "post", fake_post)

    text = licor_mod._fetch_one(_CONFIG_URL)
    assert text == _sample_json()
    assert captured["endpoint"] == "https://www.licor.cloud/api/v2/timeseriesdata"
    assert captured["body"]["dashboardUUID"] == _DASH


def test_fetch_one_http_error_returns_none(monkeypatch):
    monkeypatch.setattr(licor_mod, "_validate_url", lambda url: None)
    monkeypatch.setattr(licor_mod.requests, "post", lambda *a, **k: _FakeResp("nope", 500))
    assert licor_mod._fetch_one(_CONFIG_URL) is None


# ---------------------------------------------------------------------------
# fetch_licor — end to end (mocked POST + DNS), real DB write path
# ---------------------------------------------------------------------------


def test_fetch_licor_end_to_end_stores_all_types(session, monkeypatch):
    ensure_all_loaded()
    src, _fu = _make_licor_source(session)

    monkeypatch.setattr(licor_mod, "_validate_url", lambda url: None)
    monkeypatch.setattr(licor_mod.requests, "post", lambda *a, **k: _FakeResp(_sample_json()))
    # The CLI fn opens/closes its own sessions; hand it the test session and
    # neutralise close() so the transactional fixture survives both phases.
    monkeypatch.setattr(licor_mod, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)

    rc = fetch_licor(argparse.Namespace(dry_run=False, show_name=False))
    assert rc == 0

    obs = session.scalars(select(Observation).where(Observation.source_id == src.id)).all()
    by_type = {o.data_type: o.value for o in obs}
    assert by_type == {
        DataType.flow: pytest.approx(305.0),
        DataType.gauge: pytest.approx(6.85),
        DataType.temperature: pytest.approx(57.3),
    }


def test_fetch_licor_dry_run_writes_nothing(session, monkeypatch):
    ensure_all_loaded()
    src, _fu = _make_licor_source(session)

    monkeypatch.setattr(licor_mod, "_validate_url", lambda url: None)
    monkeypatch.setattr(licor_mod.requests, "post", lambda *a, **k: _FakeResp(_sample_json()))
    monkeypatch.setattr(licor_mod, "get_session", lambda: session)
    monkeypatch.setattr(session, "close", lambda: None)

    rc = fetch_licor(argparse.Namespace(dry_run=True, show_name=False))
    assert rc == 0
    obs = session.scalars(select(Observation).where(Observation.source_id == src.id)).all()
    assert obs == []
