"""Page cache query helpers (replaces PageDB.C)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from kayak.db.models import Page, PageAction


def store_page(
    session: Session,
    name: str,
    action: PageAction,
    body: str,
    mimetype: str = "text/html",
    expires: int | None = None,
) -> None:
    """Store or update a cached page."""
    existing = session.get(Page, name)
    if existing:
        existing.action = action
        existing.body = body
        existing.mimetype = mimetype
        existing.expires = expires
        existing.modified = datetime.utcnow()
    else:
        session.add(Page(
            name=name,
            action=action,
            body=body,
            mimetype=mimetype,
            expires=expires,
        ))


def get_page(session: Session, name: str) -> Page | None:
    """Fetch a cached page by name."""
    return session.get(Page, name)


def get_page_body(session: Session, name: str) -> str | None:
    """Fetch just the body content of a cached page."""
    page = session.get(Page, name)
    return page.body if page else None
