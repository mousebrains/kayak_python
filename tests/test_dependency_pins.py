"""Guard: tool and build-backend pins stay consistent with the locks.

1. ``[build-system].requires`` pins the build backend's *whole* resolved closure
   with ``==``. CI's requirements-build.lock drift check recompiles that list
   against live PyPI, and ``uv pip compile`` takes the newest version any
   unpinned transitive dependency admits. Pinning only hatchling (#230) still
   let the lock drift when packaging / pluggy / trove-classifiers released. A
   hatchling bump that brings a new dependency fails here, not as a red drift
   check on every later PR.
2. ``.pre-commit-config.yaml``'s ruff and mypy revs equal the versions uv.lock
   pins. That makes the hook, the dev environment, and CI's lint job (which
   reads ruff's version from uv.lock) one version. Before this guard, the hook
   and a literal pin in ci.yml sat at ruff 0.15.18 while uv.lock moved to 0.16.9.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def _norm(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _uv_lock_versions() -> dict[str, str]:
    lock = tomllib.loads((_ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {_norm(p["name"]): p["version"] for p in lock["package"]}


def _build_requires() -> list[str]:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["build-system"]["requires"]


def test_build_requires_are_exact_pins():
    loose = [r for r in _build_requires() if not _PIN.match(r)]
    assert not loose, f"[build-system].requires entries must be `name==version`: {loose}"


def test_build_requires_pin_the_whole_build_lock():
    requires = {}
    for r in _build_requires():
        m = _PIN.match(r)
        assert m, r
        requires[_norm(m.group(1))] = m.group(2)
    locked = {}
    for line in (_ROOT / "requirements-build.lock").read_text(encoding="utf-8").splitlines():
        m = _PIN.match(line)
        if m:
            locked[_norm(m.group(1))] = m.group(2)
    assert locked, "requirements-build.lock has no pinned packages"
    assert requires == locked, (
        "[build-system].requires must pin exactly the packages in requirements-build.lock "
        f"(missing from requires: {sorted(set(locked) - set(requires))}; "
        f"version mismatches: "
        f"{sorted(k for k in set(locked) & set(requires) if locked[k] != requires[k])}; "
        f"not in lock: {sorted(set(requires) - set(locked))})"
    )


def _precommit_rev(repo_suffix: str) -> str:
    config = yaml.safe_load((_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    revs = [r["rev"] for r in config["repos"] if str(r.get("repo", "")).endswith(repo_suffix)]
    assert len(revs) == 1, f"expected one {repo_suffix} repo in .pre-commit-config.yaml"
    return revs[0]


def test_precommit_ruff_matches_uv_lock():
    assert _precommit_rev("/ruff-pre-commit") == "v" + _uv_lock_versions()["ruff"]


def test_precommit_mypy_matches_uv_lock():
    assert _precommit_rev("/mirrors-mypy") == "v" + _uv_lock_versions()["mypy"]


def test_ci_lint_reads_ruff_version_from_uv_lock():
    """A literal `ruff==X.Y.Z` in ci.yml is how the lint gate drifted from uv.lock."""
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert not re.search(r"ruff==\d", ci), "ci.yml pins a literal ruff version; read uv.lock"
