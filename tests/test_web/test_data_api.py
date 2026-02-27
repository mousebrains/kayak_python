"""Tests for kayak.web.routes.data_api — JSON data API routes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from kayak.db.models import (
    DataType,
    FetchUrl,
    GaugeSource,
    LatestObservation,
    Observation,
    Source,
)
from kayak.web.app import create_app


@pytest.fixture()
def app(engine):
    """Create Flask app with test database."""
    app = create_app({"TESTING": True})
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _link_source_to_gauge(session, sample_gauge):
    """Create a Source linked to the given gauge and return the source."""
    fetch_url = FetchUrl(
        url="https://example.com/api-test", parser="usgs", is_active=True
    )
    session.add(fetch_url)
    session.flush()

    source = Source(name="api_test_source", agency="USGS", fetch_url_id=fetch_url.id)
    session.add(source)
    session.flush()

    session.add(GaugeSource(gauge_id=sample_gauge.id, source_id=source.id))
    session.flush()
    return source


def test_get_data_no_observations_returns_empty(client, session, sample_section):
    """GET /api/data/<id>/flow with no data returns empty data list."""
    source = _link_source_to_gauge(session, sample_section.gauge)
    _ = source  # ensure source is linked

    with patch("kayak.web.routes.data_api.get_session", return_value=session):
        response = client.get(f"/api/data/{sample_section.id}/flow")

    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 0
    assert data["data"] == []


def test_get_data_with_observations(client, session, sample_section):
    """GET /api/data/<id>/flow with data returns observations."""
    source = _link_source_to_gauge(session, sample_section.gauge)
    now = datetime.now(UTC)

    obs = Observation(
        source_id=source.id,
        observed_at=now - timedelta(hours=1),
        data_type=DataType.flow,
        value=1500.0,
    )
    session.add(obs)
    session.flush()

    with patch("kayak.web.routes.data_api.get_session", return_value=session):
        response = client.get(f"/api/data/{sample_section.id}/flow")

    assert response.status_code == 200
    data = response.get_json()
    assert data["count"] == 1
    assert data["data"][0]["value"] == 1500.0


def test_get_data_invalid_type_returns_400(client, session, sample_section):
    """GET /api/data/<id>/invalid_type returns 400."""
    with patch("kayak.web.routes.data_api.get_session", return_value=session):
        response = client.get(f"/api/data/{sample_section.id}/invalid_type")

    assert response.status_code == 400


def test_get_latest_with_data(client, session, sample_section):
    """GET /api/latest/<id> with data returns latest values."""
    source = _link_source_to_gauge(session, sample_section.gauge)
    now = datetime.now(UTC)

    latest = LatestObservation(
        source_id=source.id,
        data_type=DataType.flow,
        observed_at=now,
        value=2000.0,
        delta_per_hour=5.0,
        prev_value=1950.0,
        prev_observed_at=now - timedelta(hours=2),
    )
    session.add(latest)
    session.flush()

    with patch("kayak.web.routes.data_api.get_session", return_value=session):
        response = client.get(f"/api/latest/{sample_section.id}")

    assert response.status_code == 200
    data = response.get_json()
    assert "flow" in data["types"]
    assert data["types"]["flow"]["value"] == 2000.0


def test_get_latest_nonexistent_section_returns_404(client, session):
    """GET /api/latest/999 returns 404 for non-existent section."""
    with patch("kayak.web.routes.data_api.get_session", return_value=session):
        response = client.get("/api/latest/999")

    assert response.status_code == 404
