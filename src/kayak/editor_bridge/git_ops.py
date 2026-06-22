"""Thin, argv-only git wrapper for the editor → kayak_data PR bridge worker.

Tier 4 clones the dataset repo into a throwaway worktree, writes one proposal
branch, and pushes it. Every call is an explicit ``argv`` list to
``subprocess.run`` — no ``shell=True``, no string interpolation into a command —
so a malicious value in a change_request can never become a shell token.

Auth: the short-lived App installation token is passed per-command via
``-c http.extraHeader='Authorization: Basic …'`` (GitHub's HTTP Basic scheme,
username ``x-access-token``). That keeps the token **out of**: the remote URL,
``.git/config``, and the reflog — the persistent leak surfaces. It is briefly
visible in the process list (``ps``) for the command's lifetime, which is
acceptable on the single-tenant worker host; the alternative (token-in-URL)
persists it in config. Errors are scrubbed of the token + its base64 form before
they propagate (so a git failure can't print the credential into a log).
"""

from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path


class GitOpError(RuntimeError):
    """A git subprocess failed (non-zero exit), with any credential scrubbed."""


def _auth_flags(token: str | None) -> list[str]:
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=Authorization: Basic {basic}"]


def _scrub(text: str, token: str | None) -> str:
    if not token:
        return text
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return text.replace(token, "***").replace(basic, "***")


def _reject_option_like(**values: str) -> None:
    """Refuse a positional arg that begins with ``-`` (option-injection guard).

    The worker's refs/branches are deterministic (``origin/main``,
    ``editor-proposal/<id>-<n>``) and never dash-leading, but these are the
    security boundary for a worker fed change_request data — a value like
    ``--upload-pack=…`` smuggled into a positional is a known git RCE class. Cheap
    belt-and-suspenders alongside the ``--`` end-of-options separators below.
    """
    for what, value in values.items():
        if value.startswith("-"):
            raise GitOpError(f"refusing option-like {what}: {value!r}")


def _run(
    args: list[str], *, token: str | None = None, env_extra: dict[str, str] | None = None
) -> str:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"  # fail instead of hanging on a credential prompt
    # Neutralize the header-dumping trace vars so an operator's inherited
    # GIT_TRACE_CURL / GIT_CURL_VERBOSE can't surface the Authorization header
    # (the token) into output (defense in depth; output is captured + scrubbed too).
    env["GIT_TRACE_CURL"] = "0"
    env["GIT_CURL_VERBOSE"] = "0"
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(args, check=True, capture_output=True, text=True, env=env)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or f"git exited {exc.returncode}"
        raise GitOpError(_scrub(detail, token)) from None
    return proc.stdout


def clone(
    remote_url: str,
    dest: str | Path,
    *,
    token: str | None = None,
    branch: str | None = None,
    depth: int | None = None,
) -> None:
    """Clone *remote_url* into *dest*. The remote URL is stored token-free."""
    args = ["git", *_auth_flags(token), "clone", "--quiet"]
    if depth is not None:
        args += ["--depth", str(depth)]
    if branch is not None:
        _reject_option_like(branch=branch)
        args += ["--branch", branch, "--single-branch"]
    args += ["--", remote_url, str(dest)]
    _run(args, token=token)


def fetch(repo: str | Path, ref: str, *, token: str | None = None, remote: str = "origin") -> None:
    """Fetch a single *ref* from *remote* into *repo*."""
    _reject_option_like(remote=remote, ref=ref)
    _run(
        ["git", "-C", str(repo), *_auth_flags(token), "fetch", "--quiet", "--", remote, ref],
        token=token,
    )


def checkout_branch(repo: str | Path, name: str, *, start_point: str) -> None:
    """Create-or-reset local branch *name* at *start_point* (``checkout -B``).

    ``-B`` is idempotent on a fresh clone: a worker retry re-derives the same
    deterministic branch name from the same base without erroring. ``git checkout``
    has no clean ``--`` guard for a tree-ish start-point, so reject dash-leading
    values up front instead.
    """
    _reject_option_like(branch=name, start_point=start_point)
    _run(["git", "-C", str(repo), "checkout", "-q", "-B", name, start_point])


def stage(repo: str | Path, paths: list[str | Path]) -> None:
    """Stage exactly *paths* (``--`` guards a path that looks like a flag)."""
    _run(["git", "-C", str(repo), "add", "--", *[str(p) for p in paths]])


def has_staged_changes(repo: str | Path) -> bool:
    """True if the index differs from HEAD (``diff --cached --quiet`` exits 1)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if proc.returncode not in (0, 1):
        raise GitOpError(f"git diff --cached failed (exit {proc.returncode})")
    return proc.returncode == 1


def commit(
    repo: str | Path,
    message: str,
    *,
    author_name: str,
    author_email: str,
) -> str:
    """Commit the staged index with an explicit author/committer; return the SHA."""
    env_extra = {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": author_name,
        "GIT_COMMITTER_EMAIL": author_email,
    }
    _run(["git", "-C", str(repo), "commit", "-q", "-m", message], env_extra=env_extra)
    return head_sha(repo)


def head_sha(repo: str | Path) -> str:
    return _run(["git", "-C", str(repo), "rev-parse", "HEAD"]).strip()


def push(
    repo: str | Path,
    *,
    branch: str,
    remote_url: str,
    token: str | None = None,
    force: bool = False,
) -> None:
    """Push local *branch* to *remote_url* by explicit URL (nothing persists).

    Pushing to the URL (not a named remote) means the auth header and target
    never land in ``.git/config``. ``force`` is a plain ``--force`` (an
    explicit-URL push has no remote-tracking ref for ``--force-with-lease``); the
    worker only forces when re-pushing its own deterministic proposal branch.
    """
    args = ["git", "-C", str(repo), *_auth_flags(token), "push", "--quiet"]
    if force:
        args.append("--force")
    args += ["--", remote_url, f"refs/heads/{branch}:refs/heads/{branch}"]
    _run(args, token=token)
