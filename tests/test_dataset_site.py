"""Unit tests for kayak.dataset.site — the dataset site.yaml identity (S3a)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.dataset import site


class TestLoadSiteConfig:
    def test_absent_returns_engine_defaults(self, tmp_path: Path) -> None:
        # Opt-in: no site.yaml → the engine's built-in identity (current WKCC
        # values through S3), so a dataset without one renders unchanged.
        c = site.load_site_config(tmp_path)
        assert c.site_name == "WKCC River Levels"
        assert c.org_name == "Willamette Kayak and Canoe Club"
        assert c.brand_color == "#1b5591"
        assert c.attribution == "levels.wkcc.org"

    def test_empty_file_is_no_overrides(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("")
        assert site.load_site_config(tmp_path).site_name == "WKCC River Levels"

    def test_overrides_applied_partially(self, tmp_path: Path) -> None:
        # A partial site.yaml overrides only the named keys; the rest keep defaults.
        (tmp_path / site.SITE_YAML).write_text(
            'site_name: Foo Levels\nbrand_color: "#abcdef"\norg_name: Foo Paddlers\n'
        )
        c = site.load_site_config(tmp_path)
        assert c.site_name == "Foo Levels"
        assert c.brand_color == "#abcdef"
        assert c.org_name == "Foo Paddlers"
        assert c.brand_color_dark == "#0d3057"  # untouched default

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("bogus_key: 1\n")
        with pytest.raises(ValueError, match=r"bogus_key|[Ee]xtra"):
            site.load_site_config(tmp_path)

    def test_bad_hex_color_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("brand_color: blue\n")
        with pytest.raises(ValueError, match="hex color"):
            site.load_site_config(tmp_path)

    def test_non_http_org_url_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("org_url: javascript:alert(1)\n")
        with pytest.raises(ValueError, match="http"):
            site.load_site_config(tmp_path)

    def test_html_metacharacter_in_name_rejected(self, tmp_path: Path) -> None:
        # site_name lands in an og:site_name HTML attribute — reject a break-out.
        (tmp_path / site.SITE_YAML).write_text("site_name: 'Evil\"><script>'\n")
        with pytest.raises(ValueError, match="metacharacter"):
            site.load_site_config(tmp_path)

    def test_empty_name_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text('site_name: "  "\n')
        with pytest.raises(ValueError, match="non-empty"):
            site.load_site_config(tmp_path)

    def test_non_mapping_top_level_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="must be a mapping"):
            site.load_site_config(tmp_path)

    def test_malformed_yaml_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("a: : b\n")
        with pytest.raises(ValueError, match="invalid YAML"):
            site.load_site_config(tmp_path)


class TestGetSiteConfig:
    def test_reads_configured_dataset_dir(self, tmp_path: Path, monkeypatch) -> None:
        # get_site_config resolves from kayak.config.DATASET_DIR; clearing the
        # lru_cache picks up a monkeypatched dir (the documented test contract).
        monkeypatch.setattr("kayak.config.DATASET_DIR", tmp_path)
        (tmp_path / site.SITE_YAML).write_text("site_name: Cached Levels\n")
        site.get_site_config.cache_clear()
        try:
            assert site.get_site_config().site_name == "Cached Levels"
        finally:
            site.get_site_config.cache_clear()
