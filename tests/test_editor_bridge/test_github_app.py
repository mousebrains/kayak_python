"""Tests for the GitHub App auth (Tier 4 worker credential).

Real RS256 signing against a freshly-generated test keypair; the installation-
token HTTP exchange is faked (no network). Verifies the JWT claims, the token
mint happy path + the scoped-token request body, and the fail-closed errors.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import jwt
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kayak.editor_bridge.github_app import (
    GitHubAuthError,
    build_app_jwt,
    load_private_key,
    mint_installation_token,
)


@pytest.fixture(scope="module")
def keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return pem, pub


def _fake_session(status: int, payload: object) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = payload
    resp.content = b"x"
    session = MagicMock(spec=requests.Session)
    session.post.return_value = resp
    return session


# ---------------------------------------------------------------------------
# build_app_jwt
# ---------------------------------------------------------------------------


def test_build_app_jwt_claims_verify_with_public_key(keypair):
    pem, pub = keypair
    now = dt.datetime(2026, 6, 22, 12, 0, 0, tzinfo=dt.UTC)
    token = build_app_jwt(123456, pem, now=now)

    # Verify the signature but not the date claims (the fixed `now` above is not
    # the real wall clock); we assert the iat/exp values directly below.
    claims = jwt.decode(
        token, pub, algorithms=["RS256"], options={"verify_exp": False, "verify_iat": False}
    )
    assert claims["iss"] == "123456"
    # iat is backdated 60s; exp = now + 8min, so exp - iat = 60 + 480 = 540 (≤ 600 cap).
    assert claims["iat"] == int((now - dt.timedelta(seconds=60)).timestamp())
    assert claims["exp"] - claims["iat"] == 540
    assert claims["exp"] - claims["iat"] <= 600  # GitHub's hard ceiling, with headroom


def test_build_app_jwt_bad_key_raises_authentication_error():
    with pytest.raises(GitHubAuthError, match="sign"):
        build_app_jwt(1, "-----BEGIN PRIVATE KEY-----\nnotreal\n-----END PRIVATE KEY-----\n")


def test_build_app_jwt_rejects_naive_now(keypair):
    pem, _ = keypair
    with pytest.raises(GitHubAuthError, match="timezone-aware"):
        build_app_jwt(1, pem, now=dt.datetime(2026, 6, 22, 12, 0, 0))


# ---------------------------------------------------------------------------
# load_private_key
# ---------------------------------------------------------------------------


def test_load_private_key_reads_pem(tmp_path, keypair):
    pem, _ = keypair
    p = tmp_path / "app.pem"
    p.write_text(pem, encoding="utf-8")
    p.chmod(0o400)
    assert load_private_key(p) == pem


def test_load_private_key_rejects_non_pem(tmp_path):
    p = tmp_path / "junk.pem"
    p.write_text("ghp_not_a_key", encoding="utf-8")
    p.chmod(0o400)
    with pytest.raises(GitHubAuthError, match="PEM"):
        load_private_key(p)


def test_load_private_key_rejects_group_or_other_readable(tmp_path, keypair):
    # The App key is the long-lived credential; a misdeploy as 0644 must fail
    # closed (the design depends on PHP-FPM / local users not reading it).
    pem, _ = keypair
    p = tmp_path / "app.pem"
    p.write_text(pem, encoding="utf-8")
    p.chmod(0o644)
    with pytest.raises(GitHubAuthError, match="group/other-accessible"):
        load_private_key(p)


# ---------------------------------------------------------------------------
# mint_installation_token
# ---------------------------------------------------------------------------


def test_mint_token_happy_path_scopes_to_repo(keypair):
    pem, _ = keypair
    session = _fake_session(201, {"token": "ghs_abc123", "expires_at": "2026-06-22T13:00:00Z"})
    tok = mint_installation_token(
        app_id=1,
        installation_id=99,
        private_key_pem=pem,
        repository="kayak_data",
        session=session,
    )
    assert tok.token == "ghs_abc123"
    assert tok.expires_at == dt.datetime(2026, 6, 22, 13, 0, 0, tzinfo=dt.UTC)

    # Request: correct endpoint, Bearer JWT, and a token scoped to the one repo
    # with only contents+pull_requests write (defense in depth).
    _, kwargs = session.post.call_args
    assert session.post.call_args[0][0].endswith("/app/installations/99/access_tokens")
    assert kwargs["headers"]["Authorization"].startswith("Bearer ")
    assert kwargs["json"]["repositories"] == ["kayak_data"]
    assert kwargs["json"]["permissions"] == {"contents": "write", "pull_requests": "write"}


def test_mint_token_unscoped_when_no_repository(keypair):
    pem, _ = keypair
    session = _fake_session(201, {"token": "t", "expires_at": "2026-06-22T13:00:00Z"})
    mint_installation_token(app_id=1, installation_id=2, private_key_pem=pem, session=session)
    assert session.post.call_args.kwargs["json"] == {}


def test_mint_token_non_201_raises(keypair):
    pem, _ = keypair
    session = _fake_session(403, {"message": "Bad credentials"})
    with pytest.raises(GitHubAuthError, match=r"HTTP 403.*Bad credentials"):
        mint_installation_token(app_id=1, installation_id=2, private_key_pem=pem, session=session)


def test_mint_token_transport_error_raises(keypair):
    pem, _ = keypair
    session = MagicMock(spec=requests.Session)
    session.post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(GitHubAuthError, match="request failed"):
        mint_installation_token(app_id=1, installation_id=2, private_key_pem=pem, session=session)


def test_mint_token_missing_fields_raises(keypair):
    pem, _ = keypair
    session = _fake_session(201, {"token": "t"})  # no expires_at
    with pytest.raises(GitHubAuthError, match="missing token/expires_at"):
        mint_installation_token(app_id=1, installation_id=2, private_key_pem=pem, session=session)
