"""Tests for the rename-over-target deploy helpers and the in-place
``build()`` wire-up behind ``KAYAK_DEPLOY_MODE=rename``.

The symlink branch of ``build()`` is NOT exercised here — it's the prod
path and the rename work doesn't change it. These tests target the else
branch (regular-dir output) and its two modes (default vs rename).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import kayak.cli.build as build_mod
from kayak.cli.build import _deploy_staging_to_live, _sweep_orphans


def _write(p: Path, content: str, mode: int = 0o644) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    p.chmod(mode)


def test_deploy_to_empty_live(tmp_path: Path) -> None:
    """Happy path: empty live tree, three files in two dirs all land."""
    staging = tmp_path / "stage"
    live = tmp_path / "live"
    live.mkdir()
    _write(staging / "index.html", "<html>new</html>")
    _write(staging / "static/style.css", "body {}")
    _write(staging / "static/app.js", "console.log(1)")

    kept = _deploy_staging_to_live(staging, live)

    assert kept == {
        Path("index.html"),
        Path("static/style.css"),
        Path("static/app.js"),
    }
    assert (live / "index.html").read_text() == "<html>new</html>"
    assert (live / "static/style.css").read_text() == "body {}"
    assert (live / "static/app.js").read_text() == "console.log(1)"


def test_deploy_updates_existing(tmp_path: Path) -> None:
    """Files already in live get their content atomically replaced."""
    staging = tmp_path / "stage"
    live = tmp_path / "live"
    _write(live / "index.html", "<html>old</html>")
    _write(staging / "index.html", "<html>new</html>")

    # Inode before so we can assert atomic replacement, not in-place rewrite.
    old_inode = (live / "index.html").stat().st_ino

    kept = _deploy_staging_to_live(staging, live)

    assert kept == {Path("index.html")}
    assert (live / "index.html").read_text() == "<html>new</html>"
    assert (live / "index.html").stat().st_ino != old_inode


def test_deploy_preserves_mode(tmp_path: Path) -> None:
    """File mode on the source carries to the installed file."""
    staging = tmp_path / "stage"
    live = tmp_path / "live"
    live.mkdir()
    _write(staging / "script.sh", "#!/bin/sh\necho hi\n", mode=0o755)

    _deploy_staging_to_live(staging, live)

    assert (live / "script.sh").stat().st_mode & 0o777 == 0o755


def test_deploy_creates_nested_dirs(tmp_path: Path) -> None:
    """Deeply-nested paths in staging get their parents mkdir'd in live."""
    staging = tmp_path / "stage"
    live = tmp_path / "live"
    live.mkdir()
    _write(staging / "a/b/c/d.txt", "deep")

    kept = _deploy_staging_to_live(staging, live)

    assert kept == {Path("a/b/c/d.txt")}
    assert (live / "a/b/c/d.txt").read_text() == "deep"


def test_deploy_skips_empty_dirs(tmp_path: Path) -> None:
    """Empty directories in staging are not propagated."""
    staging = tmp_path / "stage"
    live = tmp_path / "live"
    live.mkdir()
    (staging / "empty").mkdir(parents=True)
    _write(staging / "real.txt", "content")

    kept = _deploy_staging_to_live(staging, live)

    assert kept == {Path("real.txt")}
    assert not (live / "empty").exists()


def test_deploy_leaves_no_dotnew_temp_files(tmp_path: Path) -> None:
    """The `.new` scratch files used for atomic rename should not remain."""
    staging = tmp_path / "stage"
    live = tmp_path / "live"
    live.mkdir()
    _write(staging / "a.html", "x")
    _write(staging / "nested/b.html", "y")

    _deploy_staging_to_live(staging, live)

    leftovers = [p for p in live.rglob("*.new") if p.is_file()]
    assert leftovers == []


def test_sweep_removes_orphans(tmp_path: Path) -> None:
    """Files in live not in kept get unlinked; kept files untouched."""
    live = tmp_path / "live"
    _write(live / "keep.html", "keep")
    _write(live / "drop.html", "drop")
    _write(live / "static/keep.css", "k")
    _write(live / "static/drop.css", "d")

    kept = {Path("keep.html"), Path("static/keep.css")}
    removed = _sweep_orphans(live, kept)

    assert set(removed) == {Path("drop.html"), Path("static/drop.css")}
    assert (live / "keep.html").exists()
    assert (live / "static/keep.css").exists()
    assert not (live / "drop.html").exists()
    assert not (live / "static/drop.css").exists()


def test_sweep_noop_when_kept_matches_live(tmp_path: Path) -> None:
    """When kept covers every file, nothing is removed."""
    live = tmp_path / "live"
    _write(live / "a.html", "a")
    _write(live / "b.html", "b")

    removed = _sweep_orphans(live, {Path("a.html"), Path("b.html")})

    assert removed == []
    assert (live / "a.html").exists()
    assert (live / "b.html").exists()


def test_deploy_then_sweep_full_cycle(tmp_path: Path) -> None:
    """End-to-end: a live dir with old content gets fully transitioned
    to the new build — updated files, new files, removed orphans."""
    staging = tmp_path / "stage"
    live = tmp_path / "live"

    # Previous build (in live)
    _write(live / "index.html", "<html>v1</html>")
    _write(live / "removed.html", "gone next build")
    _write(live / "static/old-asset.css", "old")

    # New build (in staging)
    _write(staging / "index.html", "<html>v2</html>")
    _write(staging / "new-page.html", "added this build")
    _write(staging / "static/new-asset.css", "new")

    kept = _deploy_staging_to_live(staging, live)
    removed = _sweep_orphans(live, kept)

    assert (live / "index.html").read_text() == "<html>v2</html>"
    assert (live / "new-page.html").read_text() == "added this build"
    assert (live / "static/new-asset.css").read_text() == "new"
    assert not (live / "removed.html").exists()
    assert not (live / "static/old-asset.css").exists()
    assert set(removed) == {Path("removed.html"), Path("static/old-asset.css")}


def test_deploy_skips_symlinks_in_staging(tmp_path: Path) -> None:
    """Symlinks in staging are not propagated — build output shouldn't
    contain them, and carrying a symlink over would be surprising."""
    staging = tmp_path / "stage"
    live = tmp_path / "live"
    live.mkdir()
    _write(staging / "real.txt", "content")
    (staging / "link.txt").symlink_to("real.txt")

    kept = _deploy_staging_to_live(staging, live)

    assert kept == {Path("real.txt")}
    assert not (live / "link.txt").exists()


def test_sweep_ignores_symlinks(tmp_path: Path) -> None:
    """A symlink in live is not treated as an orphan file — leave it alone
    so we don't accidentally delete unrelated operator-created links."""
    live = tmp_path / "live"
    _write(live / "target.txt", "t")
    (live / "link.txt").symlink_to("target.txt")

    removed = _sweep_orphans(live, {Path("target.txt")})

    assert removed == []
    assert (live / "link.txt").is_symlink()


def test_deploy_error_leaves_live_consistent(tmp_path: Path) -> None:
    """If a mid-deploy rename blows up, the files renamed so far are valid
    (inodes committed) and the remaining files are untouched in live.

    We simulate by making the live tree read-only partway through via a
    monkeypatched copy2 that raises on the second file.
    """
    staging = tmp_path / "stage"
    live = tmp_path / "live"
    live.mkdir()
    _write(live / "preserved.html", "original")
    _write(staging / "a.html", "new-a")
    _write(staging / "b.html", "new-b")

    orig_copy2 = build_mod.shutil.copy2
    calls: list[Path] = []

    def boom_on_second(src, dst, *a, **kw):
        calls.append(Path(dst))
        if len(calls) >= 2:
            raise OSError("simulated mid-deploy failure")
        return orig_copy2(src, dst, *a, **kw)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(build_mod.shutil, "copy2", boom_on_second)
        with pytest.raises(OSError, match="simulated mid-deploy failure"):
            _deploy_staging_to_live(staging, live)

    # Whichever file got through first is a valid file; the other is absent
    # or still a .new temp that we explicitly check isn't visible as final.
    finals = sorted(p.name for p in live.iterdir() if p.is_file())
    # preserved.html is always there; at most one of a.html/b.html made it.
    assert "preserved.html" in finals
    assert (live / "preserved.html").read_text() == "original"


# ---------------------------------------------------------------------------
# build() wire-up: KAYAK_DEPLOY_MODE=rename on the in-place branch
# ---------------------------------------------------------------------------


def _stub_build_to_dir_factory(files: dict[str, str]):
    """Factory for a `_build_to_dir` stub that writes a known file set."""

    def _stub(out_dir: Path, _args: argparse.Namespace) -> None:
        for rel, content in files.items():
            p = out_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

    return _stub


def test_build_default_mode_writes_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without KAYAK_DEPLOY_MODE set, build() falls through to the existing
    in-place write (no staging dir, no orphan sweep)."""
    live = tmp_path / "public_html"
    live.mkdir()
    # Pre-existing file — default mode doesn't sweep.
    (live / "stale.html").write_text("not removed in default mode")

    monkeypatch.setattr(
        build_mod,
        "_build_to_dir",
        _stub_build_to_dir_factory({"index.html": "v1"}),
    )
    monkeypatch.delenv("KAYAK_DEPLOY_MODE", raising=False)

    build_mod.build(argparse.Namespace(output_dir=str(live)))

    assert (live / "index.html").read_text() == "v1"
    assert (live / "stale.html").exists()  # default mode: no sweep
    assert not (tmp_path / "public_html.staging").exists()


def test_build_rename_mode_stages_then_renames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With KAYAK_DEPLOY_MODE=rename, build() stages, rename-deploys,
    sweeps orphans, and cleans up the staging dir."""
    live = tmp_path / "public_html"
    live.mkdir()
    (live / "orphan.html").write_text("will be swept")
    (live / "index.html").write_text("v0")
    old_index_inode = (live / "index.html").stat().st_ino

    monkeypatch.setattr(
        build_mod,
        "_build_to_dir",
        _stub_build_to_dir_factory(
            {
                "index.html": "v1",
                "static/app.js": "console.log(1)",
            }
        ),
    )
    monkeypatch.setenv("KAYAK_DEPLOY_MODE", "rename")

    build_mod.build(argparse.Namespace(output_dir=str(live)))

    assert (live / "index.html").read_text() == "v1"
    assert (live / "index.html").stat().st_ino != old_index_inode
    assert (live / "static/app.js").read_text() == "console.log(1)"
    assert not (live / "orphan.html").exists()
    assert not (tmp_path / "public_html.staging").exists()


def test_build_rename_mode_cleans_staging_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If _build_to_dir raises mid-build, the staging dir is torn down so a
    retry doesn't trip over stale scratch state. Live is untouched."""
    live = tmp_path / "public_html"
    live.mkdir()
    (live / "index.html").write_text("untouched")

    def _exploding_build_to_dir(out_dir: Path, _args: argparse.Namespace) -> None:
        (out_dir / "half.html").write_text("partial")
        raise RuntimeError("boom mid-build")

    monkeypatch.setattr(build_mod, "_build_to_dir", _exploding_build_to_dir)
    monkeypatch.setenv("KAYAK_DEPLOY_MODE", "rename")

    with pytest.raises(RuntimeError, match="boom mid-build"):
        build_mod.build(argparse.Namespace(output_dir=str(live)))

    # Live file unchanged — rename deploy never started.
    assert (live / "index.html").read_text() == "untouched"
    # Staging dir cleaned up by the finally block.
    assert not (tmp_path / "public_html.staging").exists()


def test_build_rename_mode_removes_stale_staging_before_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale .staging dir from a previous aborted run is wiped before
    the new build writes to it."""
    live = tmp_path / "public_html"
    live.mkdir()
    stale_staging = tmp_path / "public_html.staging"
    stale_staging.mkdir()
    (stale_staging / "leftover.html").write_text("from a prior aborted build")

    monkeypatch.setattr(
        build_mod,
        "_build_to_dir",
        _stub_build_to_dir_factory({"index.html": "fresh"}),
    )
    monkeypatch.setenv("KAYAK_DEPLOY_MODE", "rename")

    build_mod.build(argparse.Namespace(output_dir=str(live)))

    assert (live / "index.html").read_text() == "fresh"
    # The stale leftover never made it into live — it was wiped along
    # with the stale staging dir at the start of the new run.
    assert not (live / "leftover.html").exists()
    assert not stale_staging.exists()
