"""Tests for kayak.cli.fetch_usgs_ogc."""

import argparse
from unittest import mock

import pytest

from kayak.cli.fetch_usgs_ogc import (
    BATCH_SIZE,
    _build_site_map,
    _fetch_continuous,
    c_to_f,
    fetch_usgs_ogc,
)
from kayak.db.models import (
    DataType,
    FetchUrl,
    Gauge,
    GaugeSource,
    Source,
)


def _make_usgs_source(session, usgs_id="14306500", name=None):
    """Create a Source linked to a Gauge with a usgs_id."""
    fetch_url = FetchUrl(url=f"https://example.com/{usgs_id}", parser="usgs", is_active=True)
    session.add(fetch_url)
    session.flush()

    source = Source(name=name or f"usgs-{usgs_id}", agency="USGS", fetch_url_id=fetch_url.id)
    session.add(source)
    session.flush()

    gauge = Gauge(name=f"gauge-{usgs_id}", usgs_id=usgs_id)
    session.add(gauge)
    session.flush()

    session.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))
    session.flush()
    return source, gauge


def _make_geojson_response(features, next_link=None):
    """Build a GeoJSON FeatureCollection response dict."""
    resp = {
        "type": "FeatureCollection",
        "features": features,
        "links": [],
    }
    if next_link:
        resp["links"].append({"rel": "next", "href": next_link})
    return resp


def _make_feature(usgs_id, param_code, value, timestamp):
    """Build a single GeoJSON feature for the continuous collection."""
    return {
        "type": "Feature",
        "properties": {
            "monitoring_location_id": f"USGS-{usgs_id}",
            "parameter_code": param_code,
            "value": value,
            "time": timestamp,
        },
    }


# ---------------------------------------------------------------------------
# _build_site_map
# ---------------------------------------------------------------------------


def test_build_site_map(session):
    """_build_site_map returns usgs_id → source_id mapping."""
    src, _gauge = _make_usgs_source(session, usgs_id="14306500")
    site_map = _build_site_map(session)
    assert site_map == {"14306500": src.id}


def test_build_site_map_excludes_null_usgs_id(session):
    """Gauges without usgs_id are excluded from the site map."""
    fetch_url = FetchUrl(url="https://example.com/other", parser="other", is_active=True)
    session.add(fetch_url)
    session.flush()
    source = Source(name="no-usgs", agency="OTHER", fetch_url_id=fetch_url.id)
    session.add(source)
    session.flush()
    gauge = Gauge(name="gauge-no-usgs")  # no usgs_id
    session.add(gauge)
    session.flush()
    session.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))
    session.flush()

    site_map = _build_site_map(session)
    assert site_map == {}


# ---------------------------------------------------------------------------
# _fetch_continuous
# ---------------------------------------------------------------------------


def test_fetch_continuous_returns_observations(session):
    """Continuous fetch returns observation rows for known sites."""
    src, _ = _make_usgs_source(session, usgs_id="14306500")
    site_map = {"14306500": src.id}

    flow_features = [
        _make_feature("14306500", "00060", 1500.0, "2026-02-28T10:00:00Z"),
        _make_feature("14306500", "00060", 1520.0, "2026-02-28T10:15:00Z"),
    ]
    flow_response = _make_geojson_response(flow_features)
    empty_response = _make_geojson_response([])

    def mock_fetch(url, api_key):
        if "parameter_code=00060" in url:
            return flow_response
        return empty_response

    with mock.patch("kayak.cli.fetch_usgs_ogc._fetch_page", side_effect=mock_fetch):
        rows = _fetch_continuous(site_map, "test-key", 24, BATCH_SIZE)

    flow_rows = [r for r in rows if r["data_type"] == DataType.flow]
    assert len(flow_rows) == 2
    assert {r["value"] for r in flow_rows} == {1500.0, 1520.0}
    assert all(r["source_id"] == src.id for r in flow_rows)


def test_pagination(session):
    """Follows next links to fetch all pages."""
    src, _ = _make_usgs_source(session, usgs_id="14306500")
    site_map = {"14306500": src.id}

    page1 = _make_geojson_response(
        [_make_feature("14306500", "00060", 100.0, "2026-02-28T10:00:00Z")],
        next_link="https://api.waterdata.usgs.gov/ogcapi/v0/collections/continuous/items?cursor=abc",
    )
    page2 = _make_geojson_response(
        [_make_feature("14306500", "00060", 200.0, "2026-02-28T10:15:00Z")],
    )

    call_count = 0

    def mock_fetch_page(url, api_key):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return page1
        return page2

    with mock.patch("kayak.cli.fetch_usgs_ogc._fetch_page", side_effect=mock_fetch_page):
        rows = _fetch_continuous(site_map, "test-key", 24, BATCH_SIZE)

    flow_rows = [r for r in rows if r["data_type"] == DataType.flow]
    assert len(flow_rows) == 2
    # 3 param codes x 1 batch = 3 initial calls, but page2 returned for subsequent
    # The mock_fetch_page tracks calls across all param codes
    assert call_count > 2  # at least page1+page2 for first param code


def test_celsius_to_fahrenheit(session):
    """Temperature conversion from °C (param 00010) to °F."""
    src, _ = _make_usgs_source(session, usgs_id="14306500")
    site_map = {"14306500": src.id}

    # 20°C = 68°F
    features = [_make_feature("14306500", "00010", 20.0, "2026-02-28T10:00:00Z")]
    response = _make_geojson_response(features)

    # Only return data for the 00010 param code call
    def mock_fetch(url, api_key):
        if "parameter_code=00010" in url:
            return response
        return _make_geojson_response([])

    with mock.patch("kayak.cli.fetch_usgs_ogc._fetch_page", side_effect=mock_fetch):
        rows = _fetch_continuous(site_map, "test-key", 24, BATCH_SIZE)

    temp_rows = [r for r in rows if r["data_type"] == DataType.temperature]
    assert len(temp_rows) == 1
    assert temp_rows[0]["value"] == pytest.approx(68.0)


def test_unknown_site_skipped(session):
    """Observations for sites not in the site_map are skipped."""
    site_map = {}  # empty — no known sites

    features = [_make_feature("99999999", "00060", 500.0, "2026-02-28T10:00:00Z")]
    response = _make_geojson_response(features)

    with mock.patch("kayak.cli.fetch_usgs_ogc._fetch_page", return_value=response):
        rows = _fetch_continuous(site_map, "test-key", 24, BATCH_SIZE)

    assert len(rows) == 0


def test_missing_api_key(session):
    """Works without USGS_API_KEY (optional, just affects rate limit)."""
    args = argparse.Namespace(hours=24, dry_run=False, batch_size=BATCH_SIZE)

    with (
        mock.patch.dict("os.environ", {"USGS_API_KEY": ""}, clear=False),
        mock.patch("kayak.cli.fetch_usgs_ogc._build_site_map", return_value={}),
        mock.patch("kayak.cli.fetch_usgs_ogc.get_session", return_value=session),
    ):
        fetch_usgs_ogc(args)  # should return via "No USGS sites found" path


def test_c_to_f():
    """Celsius to Fahrenheit conversion is correct."""
    assert c_to_f(0.0) == pytest.approx(32.0)
    assert c_to_f(100.0) == pytest.approx(212.0)
    assert c_to_f(20.0) == pytest.approx(68.0)
    assert c_to_f(-40.0) == pytest.approx(-40.0)
