"""Tests for ``levels init-dataset`` (B5 / S5).

The command must emit a dataset that passes ``validate-dataset`` by
construction — both the blank ``scaffold`` and the ``--example`` copy — refuse a
non-empty destination, and self-validate (cleaning up) so a fresh init can never
be born invalid.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from kayak.cli import init_dataset
from kayak.cli.validate_dataset import validate_dataset
from kayak.dataset import contract
from kayak.resources import resource_dir


def _run(
    dest: Path,
    *,
    example: bool = False,
    name: str | None = None,
    dataset_id: str | None = None,
    license: str = init_dataset._DEFAULT_LICENSE,
) -> int:
    args = argparse.Namespace(
        dir=str(dest), example=example, name=name, dataset_id=dataset_id, license=license
    )
    return init_dataset._main(args)


def test_scaffold_validates_clean(tmp_path: Path) -> None:
    dest = tmp_path / "newclub"
    assert _run(dest) == 0
    # The authoritative oracle, not a re-implementation of its checks.
    assert validate_dataset(dest) == []
    meta = yaml.safe_load((dest / contract.DATASET_YAML).read_text())
    assert meta["status"] == "scaffold"
    assert meta["contract_version"] == contract.CONTRACT_VERSION
    assert meta["dataset_id"] == "newclub"  # slug of the dir name
    assert meta["name"] == "newclub"
    assert meta["engine_test_ref"] == "0" * 40


def test_scaffold_honors_identity_flags(tmp_path: Path) -> None:
    dest = tmp_path / "tndir"
    assert _run(dest, name="Tennessee Paddling", dataset_id="tn", license="CC0-1.0") == 0
    assert validate_dataset(dest) == []
    meta = yaml.safe_load((dest / contract.DATASET_YAML).read_text())
    assert (meta["name"], meta["dataset_id"], meta["license"]) == (
        "Tennessee Paddling",
        "tn",
        "CC0-1.0",
    )


def test_scaffold_slug_sanitizes_dir_name(tmp_path: Path) -> None:
    dest = tmp_path / "Smoky Mountains!"
    assert _run(dest) == 0
    meta = yaml.safe_load((dest / contract.DATASET_YAML).read_text())
    assert meta["dataset_id"] == "smoky_mountains"  # lowercased, non-alnum collapsed
    assert validate_dataset(dest) == []


def test_example_validates_and_is_byte_identical(tmp_path: Path) -> None:
    dest = tmp_path / "ex"
    assert _run(dest, example=True) == 0
    assert validate_dataset(dest) == []
    packaged = resource_dir("data", "example_dataset")
    src_files = {p.relative_to(packaged) for p in packaged.rglob("*") if p.is_file()}
    dst_files = {p.relative_to(dest) for p in dest.rglob("*") if p.is_file()}
    assert src_files == dst_files
    for rel in src_files:
        assert (packaged / rel).read_bytes() == (dest / rel).read_bytes(), rel


def test_example_ignores_identity_flags(tmp_path: Path) -> None:
    # --example copies verbatim, so --name/--id are ignored (publishable fixture).
    dest = tmp_path / "ex2"
    assert _run(dest, example=True, name="Ignored", dataset_id="ignored") == 0
    meta = yaml.safe_load((dest / contract.DATASET_YAML).read_text())
    assert meta["status"] == "publishable"
    assert meta["dataset_id"] == "fixture"


def test_refuses_nonempty_destination(tmp_path: Path) -> None:
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "stray.txt").write_text("x")
    assert _run(dest) == 2
    assert (dest / "stray.txt").exists()  # left untouched


def test_refuses_a_file_destination(tmp_path: Path) -> None:
    dest = tmp_path / "afile"
    dest.write_text("not a dir")
    assert _run(dest) == 2


def test_self_validation_guard_fires_and_removes_created_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If a generated dataset is ever invalid, init-dataset must exit non-zero and
    # leave nothing behind. Patch the validator at its source (init-dataset imports
    # it lazily at call time) to force the guard.
    monkeypatch.setattr(
        "kayak.cli.validate_dataset.validate_dataset",
        lambda d: ["deliberate: forced invalid"],
    )
    dest = tmp_path / "doomed"
    assert _run(dest) == 1
    assert not dest.exists()  # we created the root, so the whole tree is removed


def test_self_validation_guard_preserves_preexisting_empty_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "kayak.cli.validate_dataset.validate_dataset",
        lambda d: ["deliberate: forced invalid"],
    )
    dest = tmp_path / "preexisting"
    dest.mkdir()  # the operator's empty dir — keep it, only remove what we wrote
    assert _run(dest) == 1
    assert dest.is_dir()
    assert list(dest.iterdir()) == []
