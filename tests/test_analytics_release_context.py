"""Tests for kayak.analytics._release_context release-pointer inference.

The 2026-06 paired-release cutover orphaned the old
``/home/pat/public_html/index.html`` mtime signal: the file still exists
but froze at the cutover time, so ``infer_release_time`` silently returned
a stale timestamp forever. The fix reads the ``/opt/kayak/current`` symlink's
OWN mtime (the atomic-relink activation instant) via ``lstat``. These tests
pin that behavior: symlink mtime (not the target's), graceful None on a
missing pointer, and — the regression guard — a real time even when the
target is broken (the old ``stat`` would have returned None there, but the
real failure mode was a present-but-stale file, which ``lstat`` can never
silently reproduce).
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from kayak.analytics._release_context import deploy_paths_listing, infer_release_time

UTC = dt.UTC


def _set_link_mtime(link: Path, when: dt.datetime) -> None:
    ts = when.timestamp()
    os.utime(link, (ts, ts), follow_symlinks=False)


def test_infer_release_time_reads_symlink_mtime_not_target(tmp_path: Path) -> None:
    target = tmp_path / "releases" / "r1"
    target.mkdir(parents=True)
    current = tmp_path / "current"
    current.symlink_to(target)

    activated = dt.datetime(2026, 6, 17, 18, 20, 47, tzinfo=UTC)
    target_built = dt.datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    _set_link_mtime(current, activated)
    os.utime(target, (target_built.timestamp(), target_built.timestamp()))

    got = infer_release_time(current_link=current, tz=UTC)
    # Must be the symlink's own (relink) mtime, NOT the target dir's mtime.
    assert got == activated


def test_infer_release_time_missing_pointer_returns_none(tmp_path: Path) -> None:
    assert infer_release_time(current_link=tmp_path / "current", tz=UTC) is None


def test_infer_release_time_survives_broken_target(tmp_path: Path) -> None:
    # Regression guard: a momentarily-broken `current` must still yield a time
    # (lstat does not dereference), not vanish to None.
    current = tmp_path / "current"
    current.symlink_to(tmp_path / "releases" / "gone")
    activated = dt.datetime(2026, 6, 17, 18, 20, 47, tzinfo=UTC)
    _set_link_mtime(current, activated)

    assert infer_release_time(current_link=current, tz=UTC) == activated


def test_infer_release_time_rejects_non_symlink(tmp_path: Path) -> None:
    # A non-symlink at `current` (stale dir / regular file from a botched layout)
    # must NOT be trusted — its own mtime would re-seed a bogus window, the exact
    # present-but-wrong failure this signal replaced.
    real_dir = tmp_path / "current"
    real_dir.mkdir()
    os.utime(real_dir, (dt.datetime(2026, 6, 1, tzinfo=UTC).timestamp(),) * 2)
    assert infer_release_time(current_link=real_dir, tz=UTC) is None

    real_file = tmp_path / "current_file"
    real_file.write_text("x")
    assert infer_release_time(current_link=real_file, tz=UTC) is None


def test_infer_release_time_default_derives_from_release_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With no explicit path, the pointer is {HostConfig.release_root}/current, so
    # a host that relocates release_root in host.yaml is followed automatically.
    from kayak.analytics import _release_context as rc
    from kayak.host import HostConfig

    root = tmp_path / "opt" / "kayak"
    (root / "releases" / "r1").mkdir(parents=True)
    (root / "current").symlink_to(root / "releases" / "r1")
    activated = dt.datetime(2026, 6, 17, 18, 20, 47, tzinfo=UTC)
    _set_link_mtime(root / "current", activated)
    monkeypatch.setattr(rc, "get_host_config", lambda: HostConfig(release_root=str(root)))

    assert rc.infer_release_time(tz=UTC) == activated


def test_helpers_degrade_when_host_config_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # A malformed host.yaml makes get_host_config() fail-closed (ValueError).
    # analyze-logs is a read-only diagnostic, so the helpers must degrade to the
    # default release root instead of tracebacking — otherwise the friendly
    # "pass --release" path raises, and the report footer crashes even when
    # --release is supplied.
    from kayak.analytics import _release_context as rc
    from kayak.host import HostConfig

    def boom() -> HostConfig:
        raise ValueError("malformed host.yaml")

    monkeypatch.setattr(rc, "get_host_config", boom)
    assert rc._release_root() == HostConfig().release_root
    assert isinstance(rc.deploy_paths_listing(), list)  # no raise
    rc.infer_release_time()  # no raise


def test_deploy_paths_listing_shows_current_symlink(tmp_path: Path) -> None:
    target = tmp_path / "releases" / "r1"
    target.mkdir(parents=True)
    (tmp_path / "current").symlink_to(target)
    (tmp_path / "maintenance").write_text("locked")

    lines = deploy_paths_listing(layout_pattern=f"{tmp_path}/*")
    joined = "\n".join(lines)

    current_line = next(ln for ln in lines if "/current -> " in ln)
    assert current_line.startswith("l ")
    assert current_line.endswith(f"-> {target}")
    assert any(ln.startswith("d ") and ln.endswith("/releases") for ln in lines)
    assert any(ln.startswith("f ") and ln.endswith("/maintenance") for ln in lines)
    assert "public_html" not in joined


def test_deploy_paths_listing_omits_hidden_scratch(tmp_path: Path) -> None:
    # Path.glob('*') matches dotfiles; the deploy scratch (.staging, .swap.NNN)
    # is noise for a "which release is live" footer and must be skipped.
    (tmp_path / "releases").mkdir()
    (tmp_path / ".staging").mkdir()
    joined = "\n".join(deploy_paths_listing(layout_pattern=f"{tmp_path}/*"))
    assert "/releases" in joined
    assert ".staging" not in joined


def test_deploy_paths_listing_empty_when_nothing_matches(tmp_path: Path) -> None:
    assert deploy_paths_listing(layout_pattern=f"{tmp_path}/empty/*") == []
