"""Tests for kayak.config_data YAML loaders."""

from kayak.config_data import (
    load_builder_columns,
    load_description_fields,
    load_sources,
)


class TestLoadSources:
    def test_returns_list(self):
        result = load_sources()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_each_entry_has_parser_and_url(self):
        for entry in load_sources():
            assert "parser" in entry
            assert "url" in entry

    def test_cached_returns_same_object(self):
        a = load_sources()
        b = load_sources()
        assert a is b


class TestLoadBuilderColumns:
    def test_returns_list(self):
        result = load_builder_columns()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_each_entry_has_expected_keys(self):
        expected_keys = {"sort_key", "use", "type", "field"}
        for entry in load_builder_columns():
            assert expected_keys.issubset(entry.keys())

    def test_cached_returns_same_object(self):
        a = load_builder_columns()
        b = load_builder_columns()
        assert a is b


class TestLoadDescriptionFields:
    def test_returns_list(self):
        result = load_description_fields()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_each_entry_has_expected_keys(self):
        expected_keys = {"sort_key", "column", "type"}
        for entry in load_description_fields():
            assert expected_keys.issubset(entry.keys())

    def test_cached_returns_same_object(self):
        a = load_description_fields()
        b = load_description_fields()
        assert a is b
