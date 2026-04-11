"""Parser factory with decorator-based registration.

Replaces the 40-line if/else chain in fetcher.C::makeParser().
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kayak.parsers.base import BaseParser

_REGISTRY: dict[str, type[BaseParser]] = {}


def register(name: str) -> Callable[[type[BaseParser]], type[BaseParser]]:
    """Class decorator to register a parser under a given name.

    Usage::

        @register("usgs")
        class USGSParser(BaseParser):
            ...
    """

    def decorator(cls: type[BaseParser]) -> type[BaseParser]:
        _REGISTRY[name] = cls
        return cls

    return decorator


def get_parser_class(name: str) -> type[BaseParser] | None:
    """Look up a parser class by name."""
    return _REGISTRY.get(name)


def get_parser_names() -> list[str]:
    """Return sorted list of registered parser names."""
    return sorted(_REGISTRY.keys())


def ensure_all_loaded() -> None:
    """Import all parser modules to trigger registration.

    Called once at startup to populate the registry.
    """
    # Import each parser module — the @register decorator runs on import
    from kayak.parsers import (  # noqa: F401
        nwps,
        nwrfc_textplot,
        nwrfc_xml,
        usace_cda,
        usace_outflow,
        usbr,
        usgs,
        wa_gov,
    )
