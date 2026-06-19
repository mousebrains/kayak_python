"""Tests for ``levels init-dataset`` (B5 / S5).

The command must emit a dataset that passes ``validate-dataset`` by
construction — both the blank ``scaffold`` and the ``--example`` copy — refuse a
non-empty destination, and self-validate (cleaning up) so a fresh init can never
be born invalid.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest
import yaml

from kayak.cli import generate_sources as gs
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
    ci: bool = False,
    engine_repo: str = init_dataset._CI_ENGINE_REPO_PLACEHOLDER,
    engine_secret: str = init_dataset._CI_ENGINE_SECRET_DEFAULT,
    site_url: str = init_dataset._CI_SITE_URL_PLACEHOLDER,
) -> int:
    args = argparse.Namespace(
        dir=str(dest),
        example=example,
        name=name,
        dataset_id=dataset_id,
        license=license,
        ci=ci,
        engine_repo=engine_repo,
        engine_secret=engine_secret,
        site_url=site_url,
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


def test_cleanup_removes_auto_created_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # mkdir(parents=True) on a/b/c creates all three; a failure must remove the
    # whole a/ subtree, not just the leaf c/.
    monkeypatch.setattr("kayak.cli.validate_dataset.validate_dataset", lambda d: ["forced invalid"])
    top = tmp_path / "a"
    assert _run(top / "b" / "c") == 1
    assert not top.exists()


def test_write_failure_cleans_up_and_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(_dest: Path) -> None:
        raise OSError("simulated disk-full mid-write")

    monkeypatch.setattr(init_dataset, "_write_id_counters", _boom)
    dest = tmp_path / "halfwritten"
    assert _run(dest) == 1  # OSError path, not the self-validation BUG path
    assert not dest.exists()


def test_rejects_invalid_license(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = tmp_path / "ds"
    assert _run(dest, license="") == 2  # argument error, not a post-write "BUG"
    assert not dest.exists()  # rejected before anything was created
    err = capsys.readouterr().err
    assert "invalid argument" in err and "license" in err


def test_rejects_blank_id(tmp_path: Path) -> None:
    dest = tmp_path / "ds"
    assert _run(dest, dataset_id="   ") == 2
    assert not dest.exists()


def test_slug_is_ascii_only() -> None:
    assert init_dataset._slug("Smoky Mountains!") == "smoky_mountains"
    assert init_dataset._slug("café") == "caf"  # non-ASCII letters dropped
    assert init_dataset._slug("日本語") == "dataset"  # all non-ASCII → fallback


def test_ci_emits_parseable_substituted_workflow(tmp_path: Path) -> None:
    dest = tmp_path / "ds"
    assert (
        _run(
            dest,
            ci=True,
            engine_repo="myclub/kayak_python",
            engine_secret="MY_ENGINE_KEY",
            site_url="https://levels.myclub.org",
        )
        == 0
    )
    assert validate_dataset(dest) == []  # .github/ doesn't break validation
    wf = dest / ".github" / "workflows" / "validate.yml"
    doc = yaml.safe_load(wf.read_text())
    assert doc["name"] == "validate"
    text = wf.read_text()
    assert "@@" not in text  # no leftover sentinels
    assert "repository: myclub/kayak_python" in text
    assert "secrets.MY_ENGINE_KEY" in text
    assert "SITE_URL: https://levels.myclub.org" in text


def test_ci_default_carries_no_real_owner(tmp_path: Path) -> None:
    # An unedited workflow must not silently target a real org.
    dest = tmp_path / "ds"
    assert _run(dest, ci=True) == 0
    text = (dest / ".github" / "workflows" / "validate.yml").read_text()
    assert "mousebrains" not in text
    assert init_dataset._CI_ENGINE_REPO_PLACEHOLDER in text


def test_no_ci_no_workflow(tmp_path: Path) -> None:
    dest = tmp_path / "ds"
    assert _run(dest) == 0
    assert not (dest / ".github").exists()


def test_ci_rejects_malformed_params(tmp_path: Path) -> None:
    dest = tmp_path / "ds"
    assert (
        _run(dest, ci=True, engine_repo="not a repo", engine_secret="1bad", site_url="ftp://x") == 2
    )
    assert not dest.exists()  # rejected before anything was created


def test_scaffold_passes_generate_sources_check(tmp_path: Path) -> None:
    # The emitted --ci workflow runs `generate-sources --check`; a fresh scaffold
    # must be byte-identical to its own generator (the 3 generator-owned CSVs are
    # written THROUGH generate-sources, so the unused optional columns it drops
    # don't cause drift).
    dest = tmp_path / "ds"
    assert _run(dest) == 0
    rc = gs._main(argparse.Namespace(dir=str(dest), check=True, from_csv=False))
    assert rc == 0


def test_ci_pin_read_handles_scaffold_quoting(tmp_path: Path) -> None:
    # The scaffold writes a single-quoted engine_test_ref (PyYAML safe_dump); the
    # emitted workflow's pin-read must extract it (and any real SHA, quoted either
    # way). Regression for the double-quote-only sed.
    dest = tmp_path / "ds"
    assert _run(dest, ci=True) == 0
    wf = (dest / ".github" / "workflows" / "validate.yml").read_text()
    assert "[^0-9a-f]*([0-9a-f]{40})" in wf  # quote-agnostic extractor present
    # That extractor actually reads the scaffold's own (single-quoted) ref:
    line = next(
        ln
        for ln in (dest / "dataset.yaml").read_text().splitlines()
        if ln.startswith("engine_test_ref:")
    )
    m = re.match(r"engine_test_ref:[^0-9a-f]*([0-9a-f]{40})", line)
    assert m is not None and m.group(1) == "0" * 40


def test_ci_smoke_allows_scaffold(tmp_path: Path) -> None:
    # The emitted smoke must pass while the dataset is still status: scaffold.
    dest = tmp_path / "ds"
    assert _run(dest, ci=True) == 0
    wf = (dest / ".github" / "workflows" / "validate.yml").read_text()
    assert "sync-metadata --allow-scaffold" in wf


def test_example_warns_on_ignored_identity_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "ds"
    assert _run(dest, example=True, name="Ignored", dataset_id="ignored") == 0
    err = capsys.readouterr().err
    assert "ignored with --example" in err
