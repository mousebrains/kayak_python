"""Tests for the parser registry."""

from kayak.parsers.registry import (
    _REGISTRY,
    ensure_all_loaded,
    get_parser_class,
    get_parser_names,
)


class TestEnsureAllLoaded:
    def test_nwps_in_registry_after_load(self):
        """After ensure_all_loaded, 'nwps' should be in the registry."""
        ensure_all_loaded()
        assert "nwps" in _REGISTRY

    def test_get_parser_class_nwps(self):
        """get_parser_class('nwps') should return a class."""
        ensure_all_loaded()
        cls = get_parser_class("nwps")
        assert cls is not None
        assert hasattr(cls, "parse")

    def test_get_parser_class_nonexistent(self):
        """get_parser_class for unknown name should return None."""
        ensure_all_loaded()
        assert get_parser_class("nonexistent") is None

    def test_get_parser_names_sorted(self):
        """get_parser_names() should return a sorted list."""
        ensure_all_loaded()
        names = get_parser_names()
        assert names == sorted(names)
        assert len(names) >= 3

    def test_known_parsers_present(self):
        """All expected parser names should be registered."""
        ensure_all_loaded()
        names = get_parser_names()
        for expected in (
            "usbr",
            "nwrfc.xml",
            "nwrfc.textplot",
            "wa.gov",
            "usace.cda",
            "nwps",
        ):
            assert expected in names, f"{expected!r} not found in {names}"
