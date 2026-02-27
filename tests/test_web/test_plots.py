"""Tests for kayak.web.routes.plots — time-series plot routes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from kayak.db.models import (
    DataType,
    FetchUrl,
    GaugeSource,
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


def _create_flow_observations(session, gauge, count=10):
    """Create a Source with flow observations linked to the given gauge."""
    fetch_url = FetchUrl(
        url="https://example.com/plot-test", parser="usgs", is_active=True
    )
    session.add(fetch_url)
    session.flush()

    source = Source(name="plot_test_source", agency="USGS", fetch_url_id=fetch_url.id)
    session.add(source)
    session.flush()

    session.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))
    session.flush()

    now = datetime.now(UTC)
    for i in range(count):
        obs = Observation(
            source_id=source.id,
            observed_at=now - timedelta(hours=i),
            data_type=DataType.flow,
            value=1000.0 + i * 50,
        )
        session.add(obs)
    session.flush()
    return source


def test_flow_plot_returns_svg(client, session, sample_section):
    """GET /plot/flow/<id> with data returns SVG content."""
    _create_flow_observations(session, sample_section.gauge)

    with patch("kayak.web.routes.plots.get_session", return_value=session):
        response = client.get(f"/plot/flow/{sample_section.id}")

    assert response.status_code == 200
    assert response.content_type == "image/svg+xml"
    assert b"<svg" in response.data


def test_flow_plot_png_format(client, session, sample_section):
    """GET /plot/flow/<id>?fmt=png returns PNG content."""
    _create_flow_observations(session, sample_section.gauge)

    with patch("kayak.web.routes.plots.get_session", return_value=session):
        response = client.get(f"/plot/flow/{sample_section.id}?fmt=png")

    assert response.status_code == 200
    assert response.content_type == "image/png"
    assert response.data[:4] == b"\x89PNG"


def test_flow_plot_nonexistent_section_returns_404(client, session):
    """GET /plot/flow/999 returns 404 for a non-existent section."""
    with patch("kayak.web.routes.plots.get_session", return_value=session):
        response = client.get("/plot/flow/999")

    assert response.status_code == 404


def test_plot_response_has_cache_control(client, session, sample_section):
    """Plot response includes a Cache-Control header."""
    _create_flow_observations(session, sample_section.gauge)

    with patch("kayak.web.routes.plots.get_session", return_value=session):
        response = client.get(f"/plot/flow/{sample_section.id}")

    assert response.status_code == 200
    assert "max-age=" in response.headers.get("Cache-Control", "")
