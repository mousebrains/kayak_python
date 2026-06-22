"""GitHub App authentication for the editor → kayak_data PR bridge worker.

Tier 4 of ``docs/PLAN_editor_pr_bridge.md`` authenticates as a GitHub **App
installation** rather than a personal access token, for two reasons:

* **Short-lived tokens.** The long-lived secret is the App private key, which
  stays on the worker host (mode 0400, worker-user-only) and never crosses the
  wire. Each run mints a fresh *installation access token* valid ~1 hour, so a
  leaked token (in a log, a process listing) expires on its own.
* **Identity separation + a real merge gate.** The App's PRs are authored by
  ``<app>[bot]``; a human approves and merges them. Because GitHub forbids
  self-approval, "require ≥1 approving review" on the dataset repo structurally
  prevents the worker from merging its own PR — something a token's *permissions*
  alone cannot express (push-a-branch and merge-a-PR are both contents-write).

This module is pure auth: build a short-lived RS256 JWT signed with the App
private key, exchange it for an installation token, and hand back the token + its
expiry. The HTTP session and clock are injectable so tests run offline and
deterministically. No git, no DB, no PR logic.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path

import jwt
import requests

GITHUB_API_URL = "https://api.github.com"
# GitHub caps the App JWT lifetime at 10 minutes and recommends backdating ``iat``
# 60s for clock skew. 8 min + the 60s backdate gives exp-iat = 540s — comfortably
# under the 600s ceiling on both readings (iat-relative and receipt-relative).
_JWT_BACKDATE = _dt.timedelta(seconds=60)
_JWT_LIFETIME = _dt.timedelta(minutes=8)
_TOKEN_TIMEOUT = 30  # seconds


class GitHubAuthError(RuntimeError):
    """The App could not be authenticated / no installation token was minted."""


@dataclass(frozen=True)
class InstallationToken:
    """A short-lived GitHub App installation access token."""

    token: str
    expires_at: _dt.datetime  # tz-aware UTC


def load_private_key(path: str | Path) -> str:
    """Read the App private-key PEM from *path* (a 0400 worker-only file)."""
    text = Path(path).read_text(encoding="utf-8")
    if "PRIVATE KEY" not in text:
        # Fail loud on an obviously-wrong file rather than handing PyJWT garbage
        # that surfaces as an opaque signing error later.
        raise GitHubAuthError(f"{path}: does not look like a PEM private key")
    return text


def build_app_jwt(
    app_id: int,
    private_key_pem: str,
    *,
    now: _dt.datetime | None = None,
) -> str:
    """Build the short-lived RS256 App JWT (``iss``=app_id, ≤10-min ``exp``)."""
    base = now or _dt.datetime.now(_dt.UTC)
    if base.tzinfo is None:
        # A naive datetime would be interpreted in the local zone by .timestamp(),
        # silently producing wrong iat/exp; refuse it.
        raise GitHubAuthError("now must be timezone-aware")
    issued = base - _JWT_BACKDATE
    payload = {
        "iat": int(issued.timestamp()),
        "exp": int((issued + _JWT_BACKDATE + _JWT_LIFETIME).timestamp()),
        "iss": str(app_id),
    }
    try:
        return jwt.encode(payload, private_key_pem, algorithm="RS256")
    except Exception as exc:  # PyJWT raises various subclasses on a bad key
        # Never echo the key or payload; just say signing failed.
        raise GitHubAuthError("failed to sign the App JWT (bad private key?)") from exc


def mint_installation_token(
    *,
    app_id: int,
    installation_id: int,
    private_key_pem: str,
    repository: str | None = None,
    now: _dt.datetime | None = None,
    session: requests.Session | None = None,
    api_url: str = GITHUB_API_URL,
) -> InstallationToken:
    """Mint a ~1h installation access token for the App.

    When *repository* is given, the minted token is scoped to that single repo
    with only ``contents``+``pull_requests`` write — narrower than the App
    installation itself (defense in depth). The token authenticates the worker's
    ``git push`` and PR REST calls.
    """
    app_jwt = build_app_jwt(app_id, private_key_pem, now=now)
    body: dict[str, object] = {}
    if repository is not None:
        # Scope the minted token down to exactly what the worker needs.
        body["repositories"] = [repository]
        body["permissions"] = {"contents": "write", "pull_requests": "write"}

    http = session or requests
    url = f"{api_url}/app/installations/{installation_id}/access_tokens"
    try:
        resp = http.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=_TOKEN_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GitHubAuthError(f"installation-token request failed: {exc}") from exc

    if resp.status_code != 201:
        # The body can echo the request; log only status + GitHub's message field.
        raise GitHubAuthError(
            f"installation-token request returned HTTP {resp.status_code} ({_safe_message(resp)})"
        )
    data = resp.json()
    token = data.get("token")
    expires_raw = data.get("expires_at")
    if not isinstance(token, str) or not isinstance(expires_raw, str):
        raise GitHubAuthError("installation-token response missing token/expires_at")
    return InstallationToken(token=token, expires_at=_parse_iso8601_utc(expires_raw))


def _safe_message(resp: requests.Response) -> str:
    """GitHub's ``message`` field if present, else a bare marker — never the body."""
    try:
        msg = resp.json().get("message")
    except ValueError:
        return "no JSON body"
    return str(msg) if msg else "no message"


def _parse_iso8601_utc(value: str) -> _dt.datetime:
    """Parse GitHub's ``2026-01-01T00:00:00Z`` expiry into a tz-aware UTC datetime."""
    return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
