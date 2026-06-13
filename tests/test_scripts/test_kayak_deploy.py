"""kayak-deploy staging-path tests (S7 / Batch 4B, decision D2).

The activation phase needs systemd/sqlite on a real host (the clean-VM
rehearsal covers it); everything before it — ref validation,
protected-branch reachability, wheel build, dataset snapshot, contract
validation, digest manifest — is exercised here with ``--stage-only``
against local repositories, no system paths touched.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO / "deploy" / "kayak-deploy.sh"


def _run(args: list[str], env_overrides: dict[str, str], timeout: int = 600):
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        ["bash", str(_SCRIPT), *args], env=env, capture_output=True, text=True, timeout=timeout
    )


def _write_conf(tmp_path: Path, engine_repo: str, dataset_repo: str, **extra: str) -> Path:
    conf = tmp_path / "deploy.env"
    lines = [f"ENGINE_REPO={engine_repo}", f"DATASET_REPO={dataset_repo}"]
    lines += [f"{k}={v}" for k, v in extra.items()]
    conf.write_text("\n".join(lines) + "\n")
    return conf


def test_rejects_short_or_nonhex_refs(tmp_path: Path) -> None:
    conf = _write_conf(tmp_path, "/nonexistent", "/nonexistent")
    for bad in ["abc123", "main", "v1.2.0", "g" * 40]:
        proc = _run(
            ["--engine-ref", bad, "--dataset-ref", "0" * 40],
            {"KAYAK_DEPLOY_CONF": str(conf), "KAYAK_DEPLOY_ROOT": str(tmp_path / "opt")},
        )
        assert proc.returncode == 2, (bad, proc.stderr)
        assert "40-hex" in proc.stderr


def test_requires_conf_repos(tmp_path: Path) -> None:
    proc = _run(
        ["--engine-ref", "0" * 40, "--dataset-ref", "0" * 40],
        {
            "KAYAK_DEPLOY_CONF": str(tmp_path / "absent.env"),
            "KAYAK_DEPLOY_ROOT": str(tmp_path / "opt"),
        },
    )
    assert proc.returncode != 0
    assert "ENGINE_REPO" in proc.stderr


@pytest.fixture(scope="module")
def dataset_repo(tmp_path_factory) -> tuple[Path, str]:
    """A local git repo holding the committed fixture dataset."""
    root = tmp_path_factory.mktemp("dsrepo")
    repo = root / "kayak_data"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    # Copy the fixture dataset content as the repo's working tree.
    fixture = _REPO / "tests" / "fixtures" / "dataset"
    subprocess.run(["cp", "-R", f"{fixture}/.", str(repo)], check=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e",
    }
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "fixture dataset"], check=True, env=env
    )
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, sha


@pytest.mark.slow
def test_stage_only_builds_verified_release(tmp_path: Path, dataset_repo) -> None:
    """End-to-end staging: wheel from THIS engine repo's HEAD + the fixture
    dataset -> venv, contract validation, runtime config, digest manifest."""
    ds_repo, ds_sha = dataset_repo
    engine_sha = subprocess.run(
        ["git", "-C", str(_REPO), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", str(_REPO), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    conf = _write_conf(
        tmp_path,
        str(_REPO),
        str(ds_repo),
        ENGINE_BRANCH=branch,
        DATASET_BRANCH="main",
    )
    root = tmp_path / "opt"
    proc = _run(
        ["--engine-ref", engine_sha, "--dataset-ref", ds_sha, "--stage-only"],
        {
            "KAYAK_DEPLOY_CONF": str(conf),
            "KAYAK_DEPLOY_ROOT": str(root),
            # Hermetic: don't let the operator's ~/.config/kayak/.env leak in.
            "HOME": str(tmp_path),
            "SUDO_USER": "",
            # On a real host /etc/kayak/env supplies this; the fixture dataset
            # is publishable, so emit-config requires it.
            "SITE_URL": "https://levels.example.org",
        },
    )
    assert proc.returncode == 0, proc.stderr
    release_dir = Path(proc.stdout.strip().splitlines()[-1])
    assert release_dir.is_dir()

    manifest = json.loads((release_dir / "release.json").read_text())
    assert manifest["engine_sha"] == engine_sha
    assert manifest["dataset_sha"] == ds_sha
    assert manifest["deployer_version"] == 1
    assert len(manifest["wheel_sha256"]) == 64
    assert manifest["release_id"] == release_dir.name

    # The staged release is self-contained: venv with the engine, dataset
    # snapshot that passes the contract gate, non-secret runtime config.
    assert (release_dir / "venv/bin/levels").exists()
    assert (release_dir / "dataset/dataset.yaml").is_file()
    assert (release_dir / "runtime-config.json").is_file()
    assert (release_dir / manifest["wheel"]).is_file()

    # No 'current' symlink: stage-only must not activate.
    assert not (root / "current").exists()


def test_unreachable_ref_rejected(tmp_path: Path, dataset_repo) -> None:
    """A SHA not on the protected branch fails validation (the trust anchor)."""
    ds_repo, _ds_sha = dataset_repo
    bogus = "0123456789abcdef0123456789abcdef01234567"
    branch = subprocess.run(
        ["git", "-C", str(_REPO), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    conf = _write_conf(
        tmp_path, str(_REPO), str(ds_repo), ENGINE_BRANCH=branch, DATASET_BRANCH="main"
    )
    proc = _run(
        ["--engine-ref", bogus, "--dataset-ref", bogus, "--stage-only"],
        {"KAYAK_DEPLOY_CONF": str(conf), "KAYAK_DEPLOY_ROOT": str(tmp_path / "opt")},
    )
    assert proc.returncode == 1
    assert "not found" in proc.stderr or "not reachable" in proc.stderr
