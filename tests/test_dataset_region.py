"""Unit tests for kayak.dataset.region — the dataset region.yaml (S3b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.dataset import region


class TestEngineDefault:
    def test_absent_returns_engine_defaults(self, tmp_path: Path) -> None:
        # No region.yaml → generic engine defaults; regional links live in the dataset.
        r = region.load_region_config(tmp_path)
        assert r.weather_url_for("Oregon") == "https://www.windy.com/?0.0,0.0,2"
        assert r.links_for("Oregon") == []
        assert r.has_state_weather("Oregon") is False

    def test_default_weather_fallback(self, tmp_path: Path) -> None:
        r = region.load_region_config(tmp_path)
        # A state with no entry falls back to default_weather_url, label "Weather".
        assert r.weather_url_for("Nowhere") == "https://www.windy.com/?0.0,0.0,2"
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
        assert r.weather_url_for("Oregon") == "https://www.windy.com/?0.0,0.0,2"

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

    def test_trailing_whitespace_state_key_rejected(self, tmp_path: Path) -> None:
        # Exact validation: a trailing space (would build /Oregon%20.html) is rejected.
        (tmp_path / region.REGION_YAML).write_text('states:\n  "Oregon ":\n    links: []\n')
        with pytest.raises(ValueError, match="safe name"):
            region.load_region_config(tmp_path)


def test_is_safe_state_name_is_exact() -> None:
    # fullmatch, not match: the whole string must conform, so a trailing newline /
    # space / control char is rejected (review — match() let "Oregon\n" through).
    from kayak.dataset.layout import is_safe_state_name

    assert is_safe_state_name("Oregon")
    assert is_safe_state_name("New Mexico")
    for bad in ("Oregon\n", "Oregon ", " Oregon", "   ", "", "Oregon\t", "../x", "<b>"):
        assert not is_safe_state_name(bad), bad


def test_ampersand_url_allowed(tmp_path: Path) -> None:
    # Query separators are legitimate in a URL (the WKCC Dreamflows links use
    # them). This was accidentally nested inside the test above (with a stray
    # `self`) so pytest never collected it — PR #186 review caught it; the
    # rendered-HTML side is guarded in test_placeholder_state_links.py.
    (tmp_path / region.REGION_YAML).write_text(
        "states:\n  Oregon:\n    links:\n"
        "      - {label: DF, url: 'https://x.example/f?a=1&b=2#frag'}\n"
    )
    r = region.load_region_config(tmp_path)
    assert r.links_for("Oregon") == [("DF", "https://x.example/f?a=1&b=2#frag")]
