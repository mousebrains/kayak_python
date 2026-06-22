"""Minimal GitHub REST client for the editor → kayak_data PR bridge worker.

Just the calls Tier 4 needs against the dataset repo: find/open/update a PR for a
proposal branch and read a branch's / PR's state (for drift + reconcile). It is
deliberately tiny and typed; the worker depends on the :class:`GitHubClient`
Protocol so tests inject a fake with no network.

Auth is a bearer token — a short-lived App installation token from
:mod:`kayak.editor_bridge.github_app` (or, in tests, anything). This module holds
no git and no DB logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import requests

from kayak.editor_bridge.github_app import GITHUB_API_URL

_TIMEOUT = 30  # seconds


class GitHubApiError(RuntimeError):
    """A GitHub REST call failed (non-2xx or transport error)."""


@dataclass(frozen=True)
class PullRequest:
    """The slice of a GitHub PR the bridge cares about."""

    number: int
    html_url: str
    head_sha: str
    state: str  # "open" | "closed"
    merged: bool
    merge_commit_sha: str | None


class GitHubClient(Protocol):
    """What the worker needs from GitHub — implemented by :class:`RestGitHubClient`."""

    def find_open_pr(
        self, head_branch: str, *, base_branch: str | None = None
    ) -> PullRequest | None: ...

    def create_pr(
        self, *, head_branch: str, base_branch: str, title: str, body: str
    ) -> PullRequest: ...

    def update_pr(
        self, number: int, *, title: str | None = None, body: str | None = None
    ) -> PullRequest: ...

    def get_pr(self, number: int) -> PullRequest: ...

    def get_branch_sha(self, branch: str) -> str | None: ...


class RestGitHubClient:
    """:class:`GitHubClient` over the GitHub REST API, bearer-token authenticated."""

    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        token: str,
        session: requests.Session | None = None,
        api_url: str = GITHUB_API_URL,
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._http = session or requests.Session()
        self._base = f"{api_url}/repos/{owner}/{repo}"
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # -- reads ---------------------------------------------------------------

    def find_open_pr(
        self, head_branch: str, *, base_branch: str | None = None
    ) -> PullRequest | None:
        """The open PR whose head is *head_branch*, or None (idempotent reuse).

        Pass *base_branch* to make the match deterministic: GitHub allows multiple
        open PRs sharing a head branch when they target different bases, so with
        only a head filter ``per_page=1`` could return an arbitrary one.
        """
        params = {"state": "open", "head": f"{self._owner}:{head_branch}", "per_page": "1"}
        if base_branch is not None:
            params["base"] = base_branch
        rows = self._request("GET", "/pulls", params=params)
        if not isinstance(rows, list) or not rows:
            return None
        return _to_pr(rows[0])

    def get_pr(self, number: int) -> PullRequest:
        return _to_pr(self._request("GET", f"/pulls/{number}"))

    def get_branch_sha(self, branch: str) -> str | None:
        """Tip SHA of *branch*, or None if it doesn't exist (404)."""
        data = self._request("GET", f"/git/ref/heads/{branch}", allow_404=True)
        if data is None:
            return None
        obj = data.get("object") if isinstance(data, dict) else None
        sha = obj.get("sha") if isinstance(obj, dict) else None
        return sha if isinstance(sha, str) else None

    # -- writes --------------------------------------------------------------

    def create_pr(
        self, *, head_branch: str, base_branch: str, title: str, body: str
    ) -> PullRequest:
        return _to_pr(
            self._request(
                "POST",
                "/pulls",
                json={"title": title, "body": body, "head": head_branch, "base": base_branch},
            )
        )

    def update_pr(
        self, number: int, *, title: str | None = None, body: str | None = None
    ) -> PullRequest:
        payload: dict[str, object] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        return _to_pr(self._request("PATCH", f"/pulls/{number}", json=payload))

    # -- transport -----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, object] | None = None,
        allow_404: bool = False,
    ) -> object:
        try:
            resp = self._http.request(
                method,
                f"{self._base}{path}",
                params=params,
                json=json,
                headers=self._headers,
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise GitHubApiError(f"{method} {path} failed: {exc}") from exc
        if allow_404 and resp.status_code == 404:
            return None
        if not (200 <= resp.status_code < 300):
            raise GitHubApiError(
                f"{method} {path} → HTTP {resp.status_code} ({_safe_message(resp)})"
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()


def _to_pr(data: object) -> PullRequest:
    if not isinstance(data, dict):
        raise GitHubApiError("unexpected PR payload (not an object)")
    head = data.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    number = data.get("number")
    html_url = data.get("html_url")
    if (
        not isinstance(number, int)
        or not isinstance(html_url, str)
        or not isinstance(head_sha, str)
    ):
        raise GitHubApiError("PR payload missing number/html_url/head.sha")
    merge_sha = data.get("merge_commit_sha")
    return PullRequest(
        number=number,
        html_url=html_url,
        head_sha=head_sha,
        state=str(data.get("state", "")),
        merged=bool(data.get("merged", False)),
        merge_commit_sha=merge_sha if isinstance(merge_sha, str) else None,
    )


def _safe_message(resp: requests.Response) -> str:
    try:
        msg = resp.json().get("message")
    except ValueError:
        return "no JSON body"
    return str(msg) if msg else "no message"
