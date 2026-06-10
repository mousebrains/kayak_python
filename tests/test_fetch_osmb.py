"""Tests for the dataset-driven ``levels fetch-osmb`` overlay fetcher."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kayak.cli import fetch_osmb
from kayak.dataset.map import MapConfig, MapLayer


def _layer(**overrides: object) -> MapLayer:
    data = {
        "key": "hazards",
        "label": "Hazards",
        "color": "#abcdef",
        "shape": "triangle",
        "size": 12,
        "popup": "obstructions",
        "popup_link": "https://example.com/hazards",
        "output_filename": "hazards.geojson",
        "endpoint": "https://services.example.com/FeatureServer/0",
        "out_fields": ["name", "kind"],
    }
    data.update(overrides)
    return MapLayer(**data)


def _feature(lon: float, lat: float, name: str) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"name": name},
    }


def test_fetch_osmb_uses_dataset_map_layers(monkeypatch, tmp_path: Path) -> None:
    cfg = MapConfig(
        center=[41.0, -86.0],
        zoom=9,
        bbox=[-87.0, 40.0, -85.0, 42.0],
        layers=[
            _layer(
                output_filename="custom-hazards.geojson",
                endpoint="https://services.example.com/custom/FeatureServer/0",
                out_fields=["name"],
            )
        ],
    )
    monkeypatch.setattr(fetch_osmb, "get_map_config", lambda: cfg)

    calls: list[tuple[str, tuple[str, ...], fetch_osmb.BBox]] = []

    def fake_fetch_all_pages(
        base_url: str, out_fields: tuple[str, ...], bbox: fetch_osmb.BBox
    ) -> tuple[bytes, int]:
        calls.append((base_url, out_fields, bbox))
        return b'{"type":"FeatureCollection","features":[]}', 0

    monkeypatch.setattr(fetch_osmb, "_fetch_all_pages", fake_fetch_all_pages)

    fetch_osmb.fetch_osmb(argparse.Namespace(output_dir=str(tmp_path)))

    assert calls == [
        (
            "https://services.example.com/custom/FeatureServer/0",
            ("name",),
            (-87.0, 40.0, -85.0, 42.0),
        )
    ]
    assert (tmp_path / "custom-hazards.geojson").read_text(encoding="utf-8") == (
        '{"type":"FeatureCollection","features":[]}'
    )
    assert not (tmp_path / "osmb-dams.geojson").exists()


def test_fetch_osmb_noops_when_no_layers_configured(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fetch_osmb, "get_map_config", lambda: MapConfig(layers=[]))
    called = False

    def fake_fetch_all_pages(
        base_url: str, out_fields: tuple[str, ...], bbox: fetch_osmb.BBox
    ) -> tuple[bytes, int]:
        nonlocal called
        called = True
        return b"{}", 0

    monkeypatch.setattr(fetch_osmb, "_fetch_all_pages", fake_fetch_all_pages)

    fetch_osmb.fetch_osmb(argparse.Namespace(output_dir=str(tmp_path)))

    assert called is False
    assert list(tmp_path.iterdir()) == []


def test_fetch_all_pages_filters_with_configured_bbox(monkeypatch) -> None:
    pages = [
        {
            "type": "FeatureCollection",
            "features": [
                _feature(-86.0, 41.0, "inside"),
                _feature(-120.0, 44.0, "outside"),
                {"type": "Feature", "geometry": None, "properties": {"name": "bad"}},
            ],
        },
        {"type": "FeatureCollection", "features": []},
    ]
    raw_pages = [json.dumps(page).encode() for page in pages]

    def fake_fetch_page(_url: str) -> bytes:
        return raw_pages.pop(0)

    monkeypatch.setattr(fetch_osmb, "_fetch_page", fake_fetch_page)

    body, count = fetch_osmb._fetch_all_pages(
        "https://services.example.com/custom/FeatureServer/0",
        ("name",),
        (-87.0, 40.0, -85.0, 42.0),
    )

    assert count == 1
    merged = json.loads(body)
    assert [f["properties"]["name"] for f in merged["features"]] == ["inside"]
