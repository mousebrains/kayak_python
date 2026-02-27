"""Tests for kayak.web.routes.descriptions — section description routes."""

from unittest.mock import patch

import pytest

from kayak.web.app import create_app


@pytest.fixture()
def app(engine):
    """Create Flask app with test database."""
    app = create_app({"TESTING": True})
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def _mock_description_fields():
    """Return minimal description field definitions for testing."""
    return [
        {
            "sort_key": 100,
            "column": "region",
            "type": "text",
            "prefix": "Region",
            "suffix": "<br />",
        },
        {
            "sort_key": 200,
            "column": "season",
            "type": "text",
            "prefix": "Season",
            "suffix": "<br />",
        },
    ]


def test_description_valid_section_returns_html(client, session, sample_section):
    """GET /description/<id> with a valid section returns HTML."""
    # Set attributes that appear in description fields
    sample_section.region = "Pacific Northwest"
    sample_section.season = "Spring"
    session.flush()

    with (
        patch(
            "kayak.web.routes.descriptions.get_session", return_value=session
        ),
        patch(
            "kayak.web.routes.descriptions.load_description_fields",
            return_value=_mock_description_fields(),
        ),
    ):
        response = client.get(f"/description/{sample_section.id}")

    assert response.status_code == 200
    assert b"Test River - Upper" in response.data


def test_description_nonexistent_section_returns_404(client, session):
    """GET /description/999 returns 404 for a non-existent section."""
    with patch(
        "kayak.web.routes.descriptions.get_session", return_value=session
    ):
        response = client.get("/description/999")

    assert response.status_code == 404


def test_description_includes_display_name(client, session, sample_section):
    """Response HTML includes the section display_name."""
    sample_section.region = "Mountain Region"
    session.flush()

    with (
        patch(
            "kayak.web.routes.descriptions.get_session", return_value=session
        ),
        patch(
            "kayak.web.routes.descriptions.load_description_fields",
            return_value=_mock_description_fields(),
        ),
    ):
        response = client.get(f"/description/{sample_section.id}")

    assert response.status_code == 200
    html = response.data.decode()
    assert "Test River - Upper" in html
    assert "Region" in html
    assert "Mountain Region" in html
