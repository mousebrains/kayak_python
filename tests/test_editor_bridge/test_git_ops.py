"""Tests for the argv-only git wrapper (Tier 4 worker), against a local bare repo.

Exercises the real clone → branch → stage → commit → push round-trip the worker
performs, plus has_staged_changes / head_sha and the credential-scrubbing of
error output. No network: the "remote" is a bare repo on disk.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kayak.editor_bridge import git_ops
from kayak.editor_bridge.git_ops import GitOpError

_ENV = {
    "GIT_AUTHOR_NAME": "Seed",
    "GIT_AUTHOR_EMAIL": "seed@example.com",
    "GIT_COMMITTER_NAME": "Seed",
    "GIT_COMMITTER_EMAIL": "seed@example.com",
}


def _git(*args: str, cwd: Path) -> str:
    out = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**_subprocess_env(), **_ENV},
    )
    return out.stdout


def _subprocess_env() -> dict[str, str]:
    import os

    return os.environ.copy()


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """A bare repo seeded with one commit on main containing reach.csv."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    (seed / "reach.csv").write_text("id,description\n1,old\n", encoding="utf-8")
    _git("add", "-A", cwd=seed)
    _git("commit", "-q", "-m", "seed", cwd=seed)
    _git("push", "-q", "origin", "HEAD:main", cwd=seed)
    return bare


def test_clone_branch_commit_push_round_trip(origin, tmp_path):
    work = tmp_path / "work"
    git_ops.clone(str(origin), work)

    # Clean tree right after clone.
    assert git_ops.has_staged_changes(work) is False

    git_ops.checkout_branch(work, "editor-proposal/42-1", start_point="origin/main")
    (work / "reach.csv").write_text("id,description\n1,NEW\n", encoding="utf-8")
    git_ops.stage(work, ["reach.csv"])
    assert git_ops.has_staged_changes(work) is True

    sha = git_ops.commit(
        work, "editor-bridge: reach 42", author_name="bot", author_email="bot@example.com"
    )
    assert len(sha) == 40
    assert sha == git_ops.head_sha(work)

    git_ops.push(work, branch="editor-proposal/42-1", remote_url=str(origin))

    # The branch landed in the bare with the new content + the bot author.
    files = _git("ls-tree", "--name-only", "editor-proposal/42-1", cwd=origin)
    assert "reach.csv" in files
    blob = _git("show", "editor-proposal/42-1:reach.csv", cwd=origin)
    assert "NEW" in blob
    author = _git("log", "-1", "--format=%an <%ae>", "editor-proposal/42-1", cwd=origin)
    assert author.strip() == "bot <bot@example.com>"
    # main is untouched (the worker never pushes the base branch).
    assert "old" in _git("show", "main:reach.csv", cwd=origin)


def test_push_can_force_update_existing_branch(origin, tmp_path):
    # Simulate a prior run's branch already on the remote, then a retry re-pushing
    # different content to the same deterministic branch.
    work = tmp_path / "w1"
    git_ops.clone(str(origin), work)
    git_ops.checkout_branch(work, "editor-proposal/9-1", start_point="origin/main")
    (work / "reach.csv").write_text("id,description\n1,first\n", encoding="utf-8")
    git_ops.stage(work, ["reach.csv"])
    git_ops.commit(work, "first", author_name="b", author_email="b@e.com")
    git_ops.push(work, branch="editor-proposal/9-1", remote_url=str(origin))

    work2 = tmp_path / "w2"
    git_ops.clone(str(origin), work2)
    git_ops.checkout_branch(work2, "editor-proposal/9-1", start_point="origin/main")
    (work2 / "reach.csv").write_text("id,description\n1,second\n", encoding="utf-8")
    git_ops.stage(work2, ["reach.csv"])
    git_ops.commit(work2, "second", author_name="b", author_email="b@e.com")
    git_ops.push(work2, branch="editor-proposal/9-1", remote_url=str(origin), force=True)

    assert "second" in _git("show", "editor-proposal/9-1:reach.csv", cwd=origin)


def test_push_failure_scrubs_token(tmp_path):
    # Push to a nonexistent local "remote" with a token set: it must fail with a
    # GitOpError whose message never contains the token or its base64 form.
    work = tmp_path / "w"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "f").write_text("x", encoding="utf-8")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "c", cwd=work)
    secret = "supersecrettoken123"
    with pytest.raises(GitOpError) as ei:
        git_ops.push(
            work,
            branch="main",
            remote_url=str(tmp_path / "does-not-exist.git"),
            token=secret,
        )
    assert secret not in str(ei.value)


def test_clone_failure_scrubs_token(tmp_path):
    # A clone of a nonexistent remote with a token set must fail with the token
    # scrubbed (scrubbing is centralized in _run, but clone is a distinct path).
    secret = "clonesecret456"
    with pytest.raises(GitOpError) as ei:
        git_ops.clone(str(tmp_path / "nope.git"), tmp_path / "dest", token=secret)
    assert secret not in str(ei.value)


def test_fetch_and_checkout_reject_option_like_args(tmp_path):
    # Option-injection guard: a dash-leading ref / start_point is refused before
    # it can reach git as a flag (e.g. --upload-pack=…).
    work = tmp_path / "w"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    with pytest.raises(GitOpError, match="option-like"):
        git_ops.fetch(work, "--upload-pack=evil")
    with pytest.raises(GitOpError, match="option-like"):
        git_ops.checkout_branch(work, "ok", start_point="--orphan")


def test_scrub_redacts_token_and_basic_form():
    import base64

    from kayak.editor_bridge.git_ops import _scrub

    secret = "abc123"
    basic = base64.b64encode(f"x-access-token:{secret}".encode()).decode()
    msg = f"fatal: auth failed token={secret} header=Basic {basic}"
    scrubbed = _scrub(msg, secret)
    assert secret not in scrubbed
    assert basic not in scrubbed
    assert "***" in scrubbed


def test_is_ancestor(tmp_path):
    work = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    env = {**_subprocess_env(), **_ENV}
    (work / "f").write_text("a", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "a"], check=True, env=env)
    a = git_ops.head_sha(work)
    (work / "f").write_text("b", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "b"], check=True, env=env)
    b = git_ops.head_sha(work)

    assert git_ops.is_ancestor(work, a, b) is True  # a precedes b
    assert git_ops.is_ancestor(work, a, a) is True  # reflexive
    assert git_ops.is_ancestor(work, b, a) is False  # b does not precede a


def test_is_ancestor_unknown_sha_raises(tmp_path):
    work = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    env = {**_subprocess_env(), **_ENV}
    (work / "f").write_text("a", encoding="utf-8")
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "a"], check=True, env=env)
    head = git_ops.head_sha(work)
    with pytest.raises(GitOpError):
        git_ops.is_ancestor(work, "f" * 40, head)  # sha not in the repo


def test_is_ancestor_rejects_option_like(tmp_path):
    with pytest.raises(GitOpError, match="option-like"):
        git_ops.is_ancestor(tmp_path, "--help", "HEAD")
