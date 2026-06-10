"""Unit tests for kayak.dataset.map — the dataset map.yaml (S3d)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kayak.dataset import map as dataset_map

# A minimal valid layer (every required field), reused across override tests.
_LAYER_YAML = (
    "    - key: hazards\n"
    "      label: Hazards\n"
    "      color: '#abcdef'\n"
    "      shape: triangle\n"
    "      size: 12\n"
    "      popup: obstructions\n"
    "      popup_link: https://example.com/hazards\n"
    "      output_filename: hazards.geojson\n"
    "      endpoint: https://services.example.com/FeatureServer/0\n"
    "      out_fields: [name, kind]\n"
)


def _write(tmp_path: Path, body: str) -> Path:
    (tmp_path / dataset_map.MAP_YAML).write_text(body)
    return tmp_path


class TestEngineDefault:
    def test_absent_returns_engine_defaults(self, tmp_path: Path) -> None:
        # No map.yaml → generic engine defaults; regional layers live in the dataset.
        m = dataset_map.load_map_config(tmp_path)
        assert m == dataset_map._engine_default()
        assert m.center == [0.0, 0.0]
        assert m.zoom == 2
        assert m.bbox == [-180.0, -90.0, 180.0, 90.0]
        assert m.layers == []

    def test_default_fetch_and_presentation_shapes(self, tmp_path: Path) -> None:
        m = dataset_map.load_map_config(tmp_path)
        assert m.fetch_layers() == []
        assert m.presentation_layers() == []


class TestLoadMapConfig:
    def test_override_replaces_layers(self, tmp_path: Path) -> None:
        _write(tmp_path, "center: [40.0, -100.0]\nzoom: 6\nlayers:\n" + _LAYER_YAML)
        m = dataset_map.load_map_config(tmp_path)
        assert m.center == [40.0, -100.0]
        assert m.zoom == 6
        assert [layer.key for layer in m.layers] == ["hazards"]
        assert m.fetch_layers() == [
            ("hazards.geojson", "https://services.example.com/FeatureServer/0", ("name", "kind"))
        ]

    def test_empty_file_is_defaults_only(self, tmp_path: Path) -> None:
        _write(tmp_path, "")
        m = dataset_map.load_map_config(tmp_path)
        assert m.layers == []  # explicit empty file = default extent, no overlays
        assert m.center == [0.0, 0.0]

    def test_unknown_top_key_rejected(self, tmp_path: Path) -> None:
        _write(tmp_path, "bogus: 1\n")
        with pytest.raises(ValueError, match=r"bogus|[Ee]xtra"):
            dataset_map.load_map_config(tmp_path)

    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("color", "red", "hex"),
            ("color", "#ggg", "hex"),
            ("shape", "star", "shape"),
            ("popup", "rapids", "popup"),
            ("size", "0", "size"),
            ("size", "999", "size"),
            ("popup_link", "javascript:alert(1)", "http"),
            ("popup_link", 'https://e.com/"x', "metacharacter"),
            ("endpoint", "ftp://e.com/x", "http"),
            ("output_filename", "../evil.geojson", "output_filename"),
            ("output_filename", "evil.json", "output_filename"),
            ("key", "Bad Key", r"key"),
            ("label", "<b>x</b>", "metacharacter"),
        ],
    )
    def test_bad_layer_field_rejected(
        self, tmp_path: Path, field: str, value: str, match: str
    ) -> None:
        # Start from the valid layer, swap one field to a bad value.
        bad = _LAYER_YAML.replace(
            {
                "color": "color: '#abcdef'",
                "shape": "shape: triangle",
                "popup": "popup: obstructions",
                "size": "size: 12",
                "popup_link": "popup_link: https://example.com/hazards",
                "endpoint": "endpoint: https://services.example.com/FeatureServer/0",
                "output_filename": "output_filename: hazards.geojson",
                "key": "key: hazards",
                "label": "label: Hazards",
            }[field],
            f"{field}: '{value}'",
        )
        _write(tmp_path, "layers:\n" + bad)
        with pytest.raises(ValueError, match=match):
            dataset_map.load_map_config(tmp_path)

    def test_bad_out_field_rejected(self, tmp_path: Path) -> None:
        bad = _LAYER_YAML.replace("out_fields: [name, kind]", "out_fields: ['bad field']")
        _write(tmp_path, "layers:\n" + bad)
        with pytest.raises(ValueError, match="out_field"):
            dataset_map.load_map_config(tmp_path)

    @pytest.mark.parametrize("key", ["s", "c", "gauges", "__proto__", "constructor", "prototype"])
    def test_reserved_layer_key_rejected(self, tmp_path: Path, key: str) -> None:
        bad = _LAYER_YAML.replace("key: hazards", f"key: {key}")
        _write(tmp_path, "layers:\n" + bad)
        with pytest.raises(ValueError, match="reserved"):
            dataset_map.load_map_config(tmp_path)

    def test_duplicate_layer_keys_rejected(self, tmp_path: Path) -> None:
        _write(tmp_path, "layers:\n" + _LAYER_YAML + _LAYER_YAML)
        with pytest.raises(ValueError, match="duplicate"):
            dataset_map.load_map_config(tmp_path)

    def test_duplicate_output_filenames_rejected(self, tmp_path: Path) -> None:
        second = _LAYER_YAML.replace("key: hazards", "key: access_points")
        _write(tmp_path, "layers:\n" + _LAYER_YAML + second)
        with pytest.raises(ValueError, match="output_filename"):
            dataset_map.load_map_config(tmp_path)

    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ("center: [200.0, 0.0]\n", "center"),
            ("center: [0.0]\n", "center"),
            ("zoom: 25\n", "zoom"),
            ("bbox: [10.0, 10.0, 5.0, 20.0]\n", "bbox"),  # west >= east
            ("bbox: [0.0, 0.0, 0.0]\n", "bbox"),  # wrong length
        ],
    )
    def test_bad_map_field_rejected(self, tmp_path: Path, body: str, match: str) -> None:
        _write(tmp_path, body)
        with pytest.raises(ValueError, match=match):
            dataset_map.load_map_config(tmp_path)

    def test_non_mapping_top_level_rejected(self, tmp_path: Path) -> None:
        _write(tmp_path, "- a\n- b\n")
        with pytest.raises(ValueError, match="mapping"):
            dataset_map.load_map_config(tmp_path)


class TestGetMapConfig:
    def test_reads_configured_dataset_dir(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("kayak.config.DATASET_DIR", tmp_path)
        _write(tmp_path, "center: [41.0, -86.0]\nzoom: 9\nlayers:\n" + _LAYER_YAML)
        dataset_map.get_map_config.cache_clear()
        try:
            cfg = dataset_map.get_map_config()
            assert cfg.center == [41.0, -86.0]
            assert cfg.zoom == 9
            assert [layer.key for layer in cfg.layers] == ["hazards"]
        finally:
            dataset_map.get_map_config.cache_clear()


class TestBuildSiteConfig:
    def test_uses_configured_dataset_map(self, tmp_path: Path, monkeypatch) -> None:
        from kayak.web.build.site_config import build_site_config

        monkeypatch.setattr("kayak.config.DATASET_DIR", tmp_path)
        _write(tmp_path, "center: [41.0, -86.0]\nzoom: 9\nlayers:\n" + _LAYER_YAML)
        dataset_map.get_map_config.cache_clear()
        try:
            cfg = json.loads(build_site_config(lambda fn: f"/static/{fn}?v=abc"))
        finally:
            dataset_map.get_map_config.cache_clear()

        assert cfg["map"] == {"center": [41.0, -86.0], "zoom": 9}
        assert cfg["layers"] == [
            {
                "color": "#abcdef",
                "defaultOn": False,
                "key": "hazards",
                "label": "Hazards",
                "popup": "obstructions",
                "popupLink": "https://example.com/hazards",
                "shape": "triangle",
                "size": 12,
                "url": "/static/hazards.geojson?v=abc",
                "zIndex": 0,
            }
        ]
