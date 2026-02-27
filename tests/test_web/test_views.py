"""Tests for kayak.web.routes.views — section data view routes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from kayak.db.models import (
    DataType,
    FetchUrl,
    GaugeSource,
    LatestObservation,
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


def _link_source_to_gauge(session, gauge):
    """Create a Source linked to the given gauge and return the source."""
    fetch_url = FetchUrl(
        url="https://example.com/view-test", parser="usgs", is_active=True
    )
    session.add(fetch_url)
    session.flush()

    source = Source(name="view_test_source", agency="USGS", fetch_url_id=fetch_url.id)
    session.add(source)
    session.flush()

    session.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))
    session.flush()
    return source


def test_view_section_returns_html_with_name(client, session, sample_section):
    """GET /view/<id> with a valid section returns HTML containing the name."""
    with patch("kayak.web.routes.views.get_session", return_value=session):
        response = client.get(f"/view/{sample_section.id}")

    assert response.status_code == 200
    assert b"Test River - Upper" in response.data


def test_view_nonexistent_section_returns_404(client, session):
    """GET /view/999 returns 404 for a non-existent section."""
    with patch("kayak.web.routes.views.get_session", return_value=session):
        response = client.get("/view/999")

    assert response.status_code == 404


def test_view_includes_data_type_values(client, session, sample_section):
    """GET /view/<id> response includes data type values when present."""
    source = _link_source_to_gauge(session, sample_section.gauge)
    now = datetime.now(UTC)

    latest = LatestObservation(
        source_id=source.id,
        data_type=DataType.flow,
        observed_at=now,
        value=3200.5,
        delta_per_hour=10.0,
        prev_value=3100.0,
        prev_observed_at=now - timedelta(hours=2),
    )
    session.add(latest)
    session.flush()

    with patch("kayak.web.routes.views.get_session", return_value=session):
        response = client.get(f"/view/{sample_section.id}")

    assert response.status_code == 200
    assert b"3200.50" in response.data
    assert b"flow" in response.data


def test_view_html_contains_expected_elements(client, session, sample_section):
    """GET /view/<id> response contains expected HTML table elements."""
    with patch("kayak.web.routes.views.get_session", return_value=session):
        response = client.get(f"/view/{sample_section.id}")

    html = response.data.decode()
    assert "<h1>" in html
    assert "<table" in html
    assert "Type" in html
    assert "Latest Value" in html
