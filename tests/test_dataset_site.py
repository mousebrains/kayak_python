"""Unit tests for kayak.dataset.site — the dataset site.yaml identity (S3a)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.dataset import site


class TestLoadSiteConfig:
    def test_absent_returns_engine_defaults(self, tmp_path: Path) -> None:
        # Opt-in: no site.yaml → generic engine identity. Production WKCC values
        # are supplied by the WKCC dataset's explicit site.yaml.
        c = site.load_site_config(tmp_path)
        assert c.site_name == "River Levels"
        assert c.org_name == "Kayak"
        assert c.org_label == "Kayak"
        assert c.org_url == "https://example.com"
        assert c.brand_color == "#1b5591"
        assert c.attribution == "dataset contributors"
        assert c.manifest_name == "River Levels"
        assert c.manifest_short_name == "Levels"
        assert c.security_contact == ""
        assert c.security_expires == ""

    def test_empty_file_is_no_overrides(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("")
        assert site.load_site_config(tmp_path).site_name == "River Levels"

    def test_overrides_applied_partially(self, tmp_path: Path) -> None:
        # A partial site.yaml overrides only the named keys; the rest keep defaults.
        (tmp_path / site.SITE_YAML).write_text(
            'site_name: Foo Levels\nbrand_color: "#abcdef"\norg_name: Foo Paddlers\n'
        )
        c = site.load_site_config(tmp_path)
        assert c.site_name == "Foo Levels"
        assert c.brand_color == "#abcdef"
        assert c.org_name == "Foo Paddlers"
        assert c.org_label == "FP"
        assert c.manifest_name == "Foo Levels"
        assert c.brand_color_dark == "#0d3057"  # untouched default

    def test_manifest_overrides_applied(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text(
            "site_name: Foo River Levels\nmanifest_name: Foo Levels\nmanifest_short_name: Foo\n"
        )
        c = site.load_site_config(tmp_path)
        assert c.site_name == "Foo River Levels"
        assert c.manifest_name == "Foo Levels"
        assert c.manifest_short_name == "Foo"

    def test_nav_title_two_lines_accepted(self, tmp_path: Path) -> None:
        # Compact stacked header brand (1-2 lines, rendered <br>-joined).
        (tmp_path / site.SITE_YAML).write_text('nav_title: ["River", "Levels"]\n')
        assert site.load_site_config(tmp_path).nav_title == ("River", "Levels")

    def test_nav_title_default_is_empty(self, tmp_path: Path) -> None:
        # Unset → empty tuple → renderers fall back to site_name.
        assert site.load_site_config(tmp_path).nav_title == ()

    def test_nav_title_three_lines_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text('nav_title: ["a", "b", "c"]\n')
        with pytest.raises(ValueError, match="at most 2"):
            site.load_site_config(tmp_path)

    def test_nav_title_html_metacharacter_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text('nav_title: ["<b>River", "Levels"]\n')
        with pytest.raises(ValueError, match="metacharacter"):
            site.load_site_config(tmp_path)

    def test_nav_title_empty_line_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text('nav_title: ["River", "  "]\n')
        with pytest.raises(ValueError, match="non-empty"):
            site.load_site_config(tmp_path)

    def test_manifest_name_rejects_html_metacharacter(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("manifest_name: 'Foo <script>'\n")
        with pytest.raises(ValueError, match="metacharacter"):
            site.load_site_config(tmp_path)

    def test_empty_manifest_short_name_rejected(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text('manifest_short_name: "  "\n')
        with pytest.raises(ValueError, match="non-empty"):
            site.load_site_config(tmp_path)

    def test_security_txt_overrides_applied(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text(
            "security_contact: mailto:security@example.org\n"
            'security_expires: "2027-05-20T00:00:00Z"\n'
        )
        c = site.load_site_config(tmp_path)
        assert c.security_contact == "mailto:security@example.org"
        assert c.security_expires == "2027-05-20T00:00:00Z"

    def test_security_contact_rejects_line_break(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text(
            'security_contact: "mailto:security@example.org\\nPolicy: https://example.org"\n'
        )
        with pytest.raises(ValueError, match="line breaks"):
            site.load_site_config(tmp_path)

    def test_security_contact_rejects_bad_scheme(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("security_contact: ftp://example.org/report\n")
        with pytest.raises(ValueError, match="mailto"):
            site.load_site_config(tmp_path)

    def test_security_contact_rejects_whitespace(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text('security_contact: "mailto:security @example.org"\n')
        with pytest.raises(ValueError, match="whitespace"):
            site.load_site_config(tmp_path)

    def test_security_expires_rejects_bad_timestamp(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text('security_expires: "2027-99-20T00:00:00Z"\n')
        with pytest.raises(ValueError, match="valid RFC3339"):
            site.load_site_config(tmp_path)

    def test_org_label_ignores_stopwords(self, tmp_path: Path) -> None:
        (tmp_path / site.SITE_YAML).write_text("org_name: Willamette Kayak and Canoe Club\n")
        assert site.load_site_config(tmp_path).org_label == "WKCC"

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

    def test_bare_scheme_org_url_rejected(self, tmp_path: Path) -> None:
        # A scheme with no host is a broken href — require a netloc, not just a prefix.
        (tmp_path / site.SITE_YAML).write_text("org_url: http://\n")
        with pytest.raises(ValueError, match="host"):
            site.load_site_config(tmp_path)

    def test_non_string_key_reports_not_crashes(self, tmp_path: Path) -> None:
        # A YAML mapping with a non-string key (1: foo) must be a reported
        # ValueError, not a raw TypeError from SiteConfig(**data) (PR #155 review).
        (tmp_path / site.SITE_YAML).write_text("1: foo\n")
        with pytest.raises(ValueError, match="non-string key"):
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
