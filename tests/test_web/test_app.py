"""Tests for kayak.web.app — Flask application factory."""

from unittest.mock import patch

import pytest
from flask import Flask

from kayak.web.app import create_app


@pytest.fixture()
def app(engine):
    """Create Flask app with test database."""
    app = create_app({"TESTING": True})
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


def test_create_app_returns_flask_instance():
    """create_app returns a Flask application object."""
    app = create_app()
    assert isinstance(app, Flask)


def test_create_app_accepts_config_dict():
    """create_app merges the provided config dict into app.config."""
    app = create_app({"TESTING": True, "CUSTOM_KEY": "custom_value"})
    assert app.config["TESTING"] is True
    assert app.config["CUSTOM_KEY"] == "custom_value"


def test_registered_blueprints():
    """The app registers all expected blueprints."""
    app = create_app()
    blueprint_names = set(app.blueprints.keys())
    expected = {"pages", "plots", "views", "descriptions", "editing", "data_api"}
    assert expected.issubset(blueprint_names)


def test_legacy_cgi_no_args_redirects(client, session):
    """GET /cgi/display with no args redirects to the main page."""
    with patch("kayak.web.routes.pages.get_session", return_value=session):
        response = client.get("/cgi/display")
    assert response.status_code == 302
    assert "/page/main" in response.headers["Location"]


def test_legacy_cgi_page_param_redirects(client, session):
    """GET /cgi/display?P=test redirects to /page/test."""
    with patch("kayak.web.routes.pages.get_session", return_value=session):
        response = client.get("/cgi/display?P=test")
    assert response.status_code == 302
    assert "/page/test" in response.headers["Location"]
