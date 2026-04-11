"""Tests for kayak.config_data YAML loaders."""

from unittest import mock

import pytest

from kayak.config_data import (
    _load_yaml,
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


class TestLoadYamlErrors:
    def test_missing_file_raises_with_context(self, tmp_path):
        with (
            mock.patch("kayak.config_data._DATA_DIR", tmp_path),
            pytest.raises(FileNotFoundError, match=r"nonexistent\.yaml"),
        ):
            _load_yaml("nonexistent.yaml")

    def test_invalid_yaml_raises_value_error(self, tmp_path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text(":\n  - :\n  invalid: [", encoding="utf-8")
        with (
            mock.patch("kayak.config_data._DATA_DIR", tmp_path),
            pytest.raises(ValueError, match="Error parsing"),
        ):
            _load_yaml("bad.yaml")
