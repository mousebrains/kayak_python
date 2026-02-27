"""Tests for kayak.web.routes.pages — page and file serving routes."""

from unittest.mock import patch

import pytest

from kayak.db.models import Page, PageAction
from kayak.web.app import create_app


@pytest.fixture()
def app(engine):
    """Create Flask app with test database."""
    app = create_app({"TESTING": True})
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_get_page_returns_stored_body(client, session):
    """GET /page/<name> with a stored page returns 200 and the page body."""
    page = Page(
        name="test",
        action=PageAction.PAGE,
        body="<h1>Test Page</h1>",
        mimetype="text/html",
        expires=300,
    )
    session.add(page)
    session.flush()

    with patch("kayak.web.routes.pages.get_session", return_value=session):
        response = client.get("/page/test")

    assert response.status_code == 200
    assert b"<h1>Test Page</h1>" in response.data


def test_get_page_nonexistent_returns_404(client, session):
    """GET /page/<name> for a non-existent page returns 404."""
    with patch("kayak.web.routes.pages.get_session", return_value=session):
        response = client.get("/page/nonexistent")

    assert response.status_code == 404


def test_get_file_returns_correct_content_type(client, session):
    """GET /file/<name> returns the stored file with correct Content-Type."""
    page = Page(
        name="test.csv",
        action=PageAction.FILE,
        body="a,b,c\n1,2,3\n",
        mimetype="text/csv",
    )
    session.add(page)
    session.flush()

    with patch("kayak.web.routes.pages.get_session", return_value=session):
        response = client.get("/file/test.csv")

    assert response.status_code == 200
    assert response.content_type == "text/csv"
    assert b"a,b,c" in response.data


def test_root_defaults_to_main_page(client, session):
    """GET / serves the 'main' page by default."""
    page = Page(
        name="main",
        action=PageAction.PAGE,
        body="<h1>Main Page</h1>",
        mimetype="text/html",
    )
    session.add(page)
    session.flush()

    with patch("kayak.web.routes.pages.get_session", return_value=session):
        response = client.get("/")

    assert response.status_code == 200
    assert b"Main Page" in response.data


def test_page_response_includes_content_type_header(client, session):
    """Response for a stored page includes the correct Content-Type header."""
    page = Page(
        name="styled",
        action=PageAction.PAGE,
        body="body { }",
        mimetype="text/css",
        expires=600,
    )
    session.add(page)
    session.flush()

    with patch("kayak.web.routes.pages.get_session", return_value=session):
        response = client.get("/page/styled")

    assert response.status_code == 200
    assert response.content_type == "text/css"
    assert "max-age=600" in response.headers.get("Cache-Control", "")
