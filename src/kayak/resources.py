"""Filesystem access to packaged engine resources (data/ — and, after S4a-2's
later slices, php/ and static/).

These resources ship *inside* the ``kayak`` package (under ``src/kayak/``), so
``importlib.resources`` resolves them identically in an editable install
(``src/kayak/``) and an installed wheel (``site-packages/kayak/``) — there is no
repo-root ``BASE_DIR`` / source-tree lookup, which is what lets a wheel-installed
engine find them (dataset-separation plan S4a). The package is always unpacked
on disk (pip unzips wheels; the live host is a plain ``.pth`` editable install),
so the returned ``Path`` supports ``glob`` / ``iterdir`` / ``copytree``.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path


def resource_dir(*parts: str) -> Path:
    """Filesystem ``Path`` to a packaged resource under the ``kayak`` package.

    ``resource_dir("data", "db", "migrations")`` →
    ``…/kayak/data/db/migrations`` in either install layout.
    """
    root = files("kayak")
    return Path(str(root.joinpath(*parts) if parts else root))
