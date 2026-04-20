"""Deprecated facade — kept for back-compat while callers migrate.

Every function below has moved to ``kayak.db.pages``. New code should
import from there directly.
"""

from kayak.db.pages import get_page, get_page_body, store_page

__all__ = ["get_page", "get_page_body", "store_page"]
