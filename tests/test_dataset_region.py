"""Unit tests for kayak.dataset.region — the dataset region.yaml (S3b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.dataset import region


class TestEngineDefault:
    def test_absent_returns_engine_defaults(self, tmp_path: Path) -> None:
        # Opt-in: no region.yaml → the engine's built-in WKCC region data.
        r = region.load_region_config(tmp_path)
        assert r.weather_url_for("Oregon") == "https://www.windy.com/?44.0,-120.5,7"
        assert len(r.links_for("Oregon")) == 15
        assert r.has_state_weather("Oregon") is True

    def test_default_weather_fallback(self, tmp_path: Path) -> None:
        r = region.load_region_config(tmp_path)
        # A state with no entry falls back to default_weather_url, label "Weather".
        assert r.weather_url_for("Nowhere") == "https://www.windy.com/?43.0,-118.0,6"
        assert r.has_state_weather("Nowhere") is False
        assert r.links_for("Nowhere") == []


class TestLoadRegionConfig:
    def test_override_replaces_state(self, tmp_path: Path) -> None:
        (tmp_path / region.REGION_YAML).write_text(
            "default_weather_url: https://example.com/w\n"
            "states:\n"
            "  Oregon:\n"
            "    weather_url: https://example.com/or\n"
            "    links:\n"
            "      - {label: Foo Resource, url: https://foo.example}\n"
        )
        r = region.load_region_config(tmp_path)
        assert r.weather_url_for("Oregon") == "https://example.com/or"
        assert r.links_for("Oregon") == [("Foo Resource", "https://foo.example")]
        assert r.weather_url_for("Idaho") == "https://example.com/w"  # not in file → default

    def test_empty_file_is_defaults_only(self, tmp_path: Path) -> None:
        (tmp_path / region.REGION_YAML).write_text("")
        r = region.load_region_config(tmp_path)
        assert r.states == {}
        assert r.weather_url_for("Oregon") == "https://www.windy.com/?43.0,-118.0,6"

    def test_unknown_top_key_rejected(self, tmp_path: Path) -> None:
        (tmp_path / region.REGION_YAML).write_text("bogus: 1\n")
        with pytest.raises(ValueError, match=r"bogus|[Ee]xtra"):
            region.load_region_config(tmp_path)

    def test_bad_link_url_rejected(self, tmp_path: Path) -> None:
        (tmp_path / region.REGION_YAML).write_text(
            "states:\n  Oregon:\n    links:\n      - {label: X, url: javascript:alert(1)}\n"
        )
        with pytest.raises(ValueError, match="http"):
            region.load_region_config(tmp_path)

    def test_html_metacharacter_label_rejected(self, tmp_path: Path) -> None:
        (tmp_path / region.REGION_YAML).write_text(
            "states:\n  Oregon:\n    links:\n      - {label: '<script>', url: https://x.example}\n"
        )
        with pytest.raises(ValueError, match="metacharacter"):
            region.load_region_config(tmp_path)

    def test_non_string_key_reports_not_crashes(self, tmp_path: Path) -> None:
        (tmp_path / region.REGION_YAML).write_text("1: foo\n")
        with pytest.raises(ValueError, match="non-string key"):
            region.load_region_config(tmp_path)

    def test_non_mapping_top_level_rejected(self, tmp_path: Path) -> None:
        (tmp_path / region.REGION_YAML).write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            region.load_region_config(tmp_path)

    def test_path_traversal_state_key_rejected(self, tmp_path: Path) -> None:
        # A state key becomes a filename + URL path in the build, so reject one that
        # could path-traverse the staging tree (#160 review — High).
        (tmp_path / region.REGION_YAML).write_text("states:\n  ../escaped:\n    links: []\n")
        with pytest.raises(ValueError, match="safe name"):
            region.load_region_config(tmp_path)

    def test_html_metachar_state_key_rejected(self, tmp_path: Path) -> None:
        # A state key is rendered into nav/title/meta, so reject HTML metacharacters.
        (tmp_path / region.REGION_YAML).write_text(
            'states:\n  "<img src=x onerror=alert(1)>":\n    links: []\n'
        )
        with pytest.raises(ValueError, match="safe name"):
            region.load_region_config(tmp_path)

    def test_ampersand_url_allowed(self, tmp_path: Path) -> None:
        # Query separators are legitimate in a URL (the WKCC Dreamflows links use them).
        (tmp_path / region.REGION_YAML).write_text(
            "states:\n  Oregon:\n    links:\n"
            "      - {label: DF, url: 'https://x.example/f?a=1&b=2#frag'}\n"
        )
        r = region.load_region_config(tmp_path)
        assert r.links_for("Oregon") == [("DF", "https://x.example/f?a=1&b=2#frag")]
