"""Unit tests for kayak.dataset.license — dataset-owned data license labels."""

from __future__ import annotations

from pathlib import Path

import pytest

from kayak.dataset.license import load_data_license


def _write_dataset_yaml(tmp_path: Path, *, license_value: str) -> None:
    (tmp_path / "dataset.yaml").write_text(
        "contract_version: 1\n"
        "dataset_id: test\n"
        "name: Test Levels\n"
        "status: scaffold\n"
        f"license: {license_value}\n"
        'engine_test_ref: "0000000000000000000000000000000000000000"\n',
        encoding="utf-8",
    )


def test_missing_dataset_yaml_uses_generic_fallback(tmp_path: Path) -> None:
    license_meta = load_data_license(tmp_path)

    assert license_meta.identifier == "CC-BY-NC-4.0"
    assert license_meta.label == "CC BY-NC 4.0"
    assert license_meta.url == "https://creativecommons.org/licenses/by-nc/4.0/"


def test_known_dataset_license_gets_canonical_label_and_url(tmp_path: Path) -> None:
    _write_dataset_yaml(tmp_path, license_value="CC0-1.0")

    license_meta = load_data_license(tmp_path)

    assert license_meta.identifier == "CC0-1.0"
    assert license_meta.label == "CC0 1.0"
    assert license_meta.url == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert license_meta.notice == (
        "Metadata + calculated values: CC0 1.0. Observations: public domain at source."
    )


def test_unknown_dataset_license_renders_literal_label(tmp_path: Path) -> None:
    _write_dataset_yaml(tmp_path, license_value="'Open Data License 1.0'")

    license_meta = load_data_license(tmp_path)

    assert license_meta.identifier == "Open Data License 1.0"
    assert license_meta.label == "Open Data License 1.0"
    assert license_meta.url == ""


def test_empty_dataset_license_is_rejected(tmp_path: Path) -> None:
    _write_dataset_yaml(tmp_path, license_value="''")

    with pytest.raises(ValueError, match="license must be a non-empty string"):
        load_data_license(tmp_path)
