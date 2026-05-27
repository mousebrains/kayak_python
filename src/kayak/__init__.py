"""Kayak - River level data aggregation from government agencies."""

from importlib.metadata import PackageNotFoundError, version


def _detect_version() -> str:
    """Installed dist version, or a sentinel when run from a bare source tree."""
    try:
        return version("kayak")
    except PackageNotFoundError:  # no installed dist (e.g. plain pythonpath=src)
        return "0+unknown"


__version__ = _detect_version()
