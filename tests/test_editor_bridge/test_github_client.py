"""Tests for the minimal GitHub REST client (Tier 4 worker).

A fake ``requests.Session`` captures the request and returns canned responses;
no network. Covers PR find/create/update/get, branch-ref lookup (incl. 404 →
None), and the fail-closed error on a non-2xx.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from kayak.editor_bridge.github_client import (
    GitHubApiError,
    RestGitHubClient,
)


def _resp(status: int, payload: object = None) -> MagicMock:
    r = MagicMock(spec=requests.Response)
    r.status_code = status
    r.content = b"" if payload is None else b"x"
    r.json.return_value = payload
    return r


def _client(session: MagicMock) -> RestGitHubClient:
    return RestGitHubClient(owner="mousebrains", repo="kayak_data", token="ghs_x", session=session)


_PR_JSON = {
    "number": 7,
    "html_url": "https://github.com/mousebrains/kayak_data/pull/7",
    "state": "open",
    "merged": False,
    "merge_commit_sha": None,
    "head": {"sha": "abc123"},
}


def test_find_open_pr_returns_match():
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(200, [_PR_JSON])
    pr = _client(session).find_open_pr("editor-proposal/42-1")

    assert pr is not None
    assert (pr.number, pr.head_sha, pr.state) == (7, "abc123", "open")
    method, url = session.request.call_args[0]
    assert method == "GET" and url.endswith("/repos/mousebrains/kayak_data/pulls")
    # head filter is owner-qualified
    assert session.request.call_args.kwargs["params"]["head"] == "mousebrains:editor-proposal/42-1"


def test_find_open_pr_empty_returns_none():
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(200, [])
    assert _client(session).find_open_pr("nope") is None


def test_find_open_pr_filters_by_base_when_given():
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(200, [_PR_JSON])
    _client(session).find_open_pr("editor-proposal/42-1", base_branch="main")
    params = session.request.call_args.kwargs["params"]
    assert params["base"] == "main"
    assert params["head"] == "mousebrains:editor-proposal/42-1"


def test_create_pr_posts_and_parses():
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(201, _PR_JSON)
    pr = _client(session).create_pr(
        head_branch="editor-proposal/42-1", base_branch="main", title="T", body="B"
    )
    assert pr.number == 7
    method, url = session.request.call_args[0]
    assert method == "POST" and url.endswith("/pulls")
    body = session.request.call_args.kwargs["json"]
    assert body == {"title": "T", "body": "B", "head": "editor-proposal/42-1", "base": "main"}


def test_update_pr_patches_only_given_fields():
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(200, _PR_JSON)
    _client(session).update_pr(7, body="new body")
    method, url = session.request.call_args[0]
    assert method == "PATCH" and url.endswith("/pulls/7")
    assert session.request.call_args.kwargs["json"] == {"body": "new body"}


def test_get_pr_reports_merged_state():
    merged = {**_PR_JSON, "state": "closed", "merged": True, "merge_commit_sha": "deadbeef"}
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(200, merged)
    pr = _client(session).get_pr(7)
    assert pr.merged is True
    assert pr.merge_commit_sha == "deadbeef"


def test_get_branch_sha_found_and_missing():
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(200, {"object": {"sha": "feedface"}})
    assert _client(session).get_branch_sha("main") == "feedface"

    session.request.return_value = _resp(404, {"message": "Not Found"})
    assert _client(session).get_branch_sha("ghost") is None


def test_non_2xx_raises_api_error():
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(422, {"message": "Validation failed"})
    with pytest.raises(GitHubApiError, match=r"HTTP 422.*Validation failed"):
        _client(session).create_pr(head_branch="h", base_branch="main", title="t", body="b")


def test_transport_error_raises_api_error():
    session = MagicMock(spec=requests.Session)
    session.request.side_effect = requests.Timeout("slow")
    with pytest.raises(GitHubApiError, match="failed"):
        _client(session).get_pr(1)


def test_malformed_pr_payload_raises():
    session = MagicMock(spec=requests.Session)
    session.request.return_value = _resp(200, {"number": 7})  # no html_url / head.sha
    with pytest.raises(GitHubApiError, match=r"missing number/html_url/head\.sha"):
        _client(session).get_pr(7)
