"""Page routes (replaces CMD::page, CMD::file)."""

from flask import Blueprint, abort, make_response

from kayak.db.engine import get_session
from kayak.db.page_db import get_page

pages_bp = Blueprint("pages", __name__)


@pages_bp.route("/")
@pages_bp.route("/page/<name>")
def page(name: str = "main"):
    """Serve a cached page by name."""
    session = get_session()
    try:
        pg = get_page(session, name)
        if pg is None:
            abort(404)
        resp = make_response(pg.body or "")
        resp.headers["Content-Type"] = pg.mimetype or "text/html"
        if pg.expires:
            resp.headers["Cache-Control"] = f"max-age={abs(pg.expires)}"
        return resp
    finally:
        session.close()


@pages_bp.route("/file/<name>")
def file_page(name: str):
    """Serve a cached file (CSV, text, etc.)."""
    session = get_session()
    try:
        pg = get_page(session, name)
        if pg is None:
            abort(404)
        resp = make_response(pg.body or "")
        resp.headers["Content-Type"] = pg.mimetype or "application/octet-stream"
        return resp
    finally:
        session.close()
