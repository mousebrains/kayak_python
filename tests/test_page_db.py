"""Tests for kayak.db.page_db cache helpers."""

from kayak.db.models import PageAction
from kayak.db.page_db import get_page, get_page_body, store_page


def test_store_and_retrieve(session):
    """store_page creates a Page that get_page can retrieve."""
    store_page(session, "main", PageAction.PAGE, "<h1>Hello</h1>")
    session.flush()

    page = get_page(session, "main")
    assert page is not None
    assert page.name == "main"
    assert page.action == PageAction.PAGE
    assert page.body == "<h1>Hello</h1>"


def test_get_page_nonexistent(session):
    """get_page returns None for a name that does not exist."""
    assert get_page(session, "nope") is None


def test_get_page_body_returns_string(session):
    """get_page_body returns just the body text, not the full Page object."""
    store_page(session, "body_test", PageAction.FILE, "csv,data")
    session.flush()

    body = get_page_body(session, "body_test")
    assert body == "csv,data"
    assert isinstance(body, str)


def test_store_page_upsert(session):
    """Calling store_page twice with the same name updates the existing row."""
    store_page(session, "upsert", PageAction.PAGE, "version1")
    session.flush()

    store_page(session, "upsert", PageAction.PAGE, "version2")
    session.flush()

    page = get_page(session, "upsert")
    assert page is not None
    assert page.body == "version2"


def test_store_page_with_expires_and_mimetype(session):
    """store_page honours the expires and mimetype parameters."""
    store_page(
        session,
        "styled",
        PageAction.FILE,
        "body content",
        mimetype="text/csv",
        expires=3600,
    )
    session.flush()

    page = get_page(session, "styled")
    assert page is not None
    assert page.mimetype == "text/csv"
    assert page.expires == 3600
