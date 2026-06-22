"""Tests for the editor → kayak_data PR bridge worker (Tier 4).

End-to-end against a **local bare git repo** as the "remote" (no network) and a
**fake GitHub client** (the GitHubClient Protocol). Covers the happy path (queued
→ PR opened + branch pushed), drift → conflict, idempotent PR reuse, the no-op
case, and an empty queue.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
from pathlib import Path

import pytest

from kayak.config import KayakConfig
from kayak.db.models import (
    BridgeState,
    ChangeRequest,
    ChangeRequestBridge,
    ChangeStatus,
    ChangeTarget,
)
from kayak.editor_bridge import git_ops, worker
from kayak.editor_bridge.github_client import GitHubApiError, PullRequest

_CLOCK = lambda: dt.datetime(2026, 6, 22, 12, 0, 0, tzinfo=dt.UTC)  # noqa: E731

# Captured before the autouse stub below rebinds it, so the dedicated email test
# can exercise the real helper while every other test gets a no-op (so a
# mark-deployed run never spawns `mail`).
_REAL_NOTIFY = worker._notify_proposer_deployed


@pytest.fixture(autouse=True)
def _stub_deploy_email(monkeypatch):
    """No worker test may spawn `mail`. Default the deploy-email to a no-op; the
    email test calls _REAL_NOTIFY directly with a captured subprocess."""
    monkeypatch.setattr(worker, "_notify_proposer_deployed", lambda *a, **k: None)


# ---------------------------------------------------------------------------
# fakes / fixtures
# ---------------------------------------------------------------------------


class FakeGitHubClient:
    """In-memory GitHubClient: records created/updated PRs, no network."""

    def __init__(self) -> None:
        self.prs: dict[int, PullRequest] = {}
        self.by_head: dict[str, int] = {}
        self.created: list[tuple[str, str]] = []
        self.updated: list[tuple[int, str | None]] = []
        self._next = 1
        self.raise_get_pr = False  # reconcile error-path testing

    def seed_open_pr(self, head_branch: str) -> PullRequest:
        return self.create_pr(head_branch=head_branch, base_branch="main", title="seed", body="")

    def find_open_pr(self, head_branch, *, base_branch=None):
        n = self.by_head.get(head_branch)
        return self.prs.get(n) if n is not None else None

    def create_pr(self, *, head_branch, base_branch, title, body):
        n = self._next
        self._next += 1
        pr = PullRequest(
            number=n,
            html_url=f"https://github.com/testowner/testrepo/pull/{n}",
            head_sha="0" * 40,
            state="open",
            merged=False,
            merge_commit_sha=None,
        )
        self.prs[n] = pr
        self.by_head[head_branch] = n
        self.created.append((head_branch, title))
        return pr

    def update_pr(self, number, *, title=None, body=None):
        self.updated.append((number, title))
        return self.prs[number]

    def get_pr(self, number):
        if self.raise_get_pr:
            raise GitHubApiError("simulated PR read failure")
        return self.prs[number]

    def set_pr(self, number, *, state, merged, merge_commit_sha=None):
        self.prs[number] = PullRequest(
            number=number,
            html_url=f"https://github.com/testowner/testrepo/pull/{number}",
            head_sha="0" * 40,
            state=state,
            merged=merged,
            merge_commit_sha=merge_commit_sha,
        )

    def get_branch_sha(self, branch):
        return None


def _cfg(review_url: str | None = None) -> KayakConfig:
    return KayakConfig(
        editor_bridge_enabled=True,
        editor_bridge_dataset_owner="testowner",
        editor_bridge_dataset_name="testrepo",
        editor_bridge_base_branch="main",
        editor_bridge_branch_prefix="editor-proposal/",
        editor_bridge_review_url=review_url,
    )


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    """Bare 'remote' on main with reach.csv (ids 42, 43) + gauge.csv (id 7)."""
    import os

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "seed",
        "GIT_AUTHOR_EMAIL": "seed@example.com",
        "GIT_COMMITTER_NAME": "seed",
        "GIT_COMMITTER_EMAIL": "seed@example.com",
    }
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True)
    (seed / "reach.csv").write_text(
        "id,updated_at,description\n42,2026-01-01,old desc\n43,2026-01-01,other desc\n",
        encoding="utf-8",
    )
    (seed / "gauge.csv").write_text("id,name,location\n7,G7,old loc\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(seed), "commit", "-q", "-m", "seed"], check=True, env=env)
    subprocess.run(
        ["git", "-C", str(seed), "push", "-q", "origin", "HEAD:main"], check=True, env=env
    )
    return bare


def _show(origin: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "-C", str(origin), "show", ref], check=True, capture_output=True, text=True
    ).stdout


def _seed_request(
    session,
    editor_id: int,
    *,
    applied: str,
    base: str | None,
    target_type: ChangeTarget = ChangeTarget.reach,
    target_id: int = 42,
    status: str = "approved",
    with_sha: bool = True,
) -> ChangeRequestBridge:
    cr = ChangeRequest(
        target_type=target_type,
        target_id=target_id,
        editor_id=editor_id,
        subject="update",
        payload_json=applied,
        status=status,
        applied_json=applied,
        # A fixed endorse time → the worker stamps a row-stable updated_at + commit
        # date (so a retry reproduces the same content + SHA). Naive UTC, as SQLite stores.
        reviewed_at=dt.datetime(2026, 6, 22, 12, 0, 0),
    )
    session.add(cr)
    session.flush()
    # Tier 2 pins sha256(applied_json) at queue time; mirror it so the worker's
    # tamper guard passes (with_sha=False seeds a row without one).
    sha = hashlib.sha256(applied.encode("utf-8")).hexdigest() if with_sha else None
    bridge = ChangeRequestBridge(
        change_request_id=cr.id,
        state=BridgeState.queued,
        reviewed_base_json=base,
        applied_json_sha256=sha,
    )
    session.add(bridge)
    session.flush()
    return bridge


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_happy_path_opens_pr_and_pushes_branch(session, editor, origin):
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new desc"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    client = FakeGitHubClient()
    outcomes = worker.run_once(
        session,
        _cfg("https://levels.example.org"),
        client=client,
        clone_url=str(origin),
        clock=_CLOCK,
    )

    assert len(outcomes) == 1
    assert outcomes[0].state == "pr_open"
    assert outcomes[0].pr_number == 1

    session.refresh(bridge)
    assert bridge.state == BridgeState.pr_open
    assert bridge.branch_name == f"editor-proposal/{bridge.change_request_id}-1"
    assert bridge.pr_number == 1
    assert bridge.pr_url.endswith("/pull/1")
    assert len(bridge.pr_head_sha) == 40

    # The proposal branch landed in the remote with the edit; main is untouched.
    assert "new desc" in _show(origin, f"{bridge.branch_name}:reach.csv")
    assert "old desc" in _show(origin, "main:reach.csv")
    # updated_at was stamped in the dataset's "YYYY-MM-DD HH:MM:SS" format.
    assert "2026-06-22 12:00:00" in _show(origin, f"{bridge.branch_name}:reach.csv")
    # PR created (not updated), authored by the bot.
    assert len(client.created) == 1 and not client.updated
    author = subprocess.run(
        ["git", "-C", str(origin), "log", "-1", "--format=%an <%ae>", bridge.branch_name],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "editor-bridge" in author


def test_drift_marks_conflict_and_opens_no_pr(session, editor, origin):
    # reviewed base ("WAS HERE") doesn't match the remote's "old desc" → drift.
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new desc"}}',
        base='{"reach": {"description": "WAS HERE"}}',
    )
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "conflict"
    session.refresh(bridge)
    assert bridge.state == BridgeState.conflict
    assert "drift" in (bridge.last_error or "").lower()
    assert bridge.conflict_json is not None
    assert client.created == []  # no PR for a drifted proposal


def test_reuses_existing_open_pr(session, editor, origin):
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new desc"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    client = FakeGitHubClient()
    branch = f"editor-proposal/{bridge.change_request_id}-1"
    existing = client.seed_open_pr(branch)  # a prior run already opened it

    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "pr_open"
    assert outcome.pr_number == existing.number
    # Updated the existing PR, did not create a second one.
    assert client.updated and client.updated[-1][0] == existing.number
    assert len(client.created) == 1  # only the seed


def test_noop_proposal_marks_worker_error(session, editor, origin):
    # The proposal already matches the remote ("old desc") → nothing to commit.
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "old desc"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "worker_error"
    session.refresh(bridge)
    assert bridge.state == BridgeState.worker_error
    assert "no-op" in (bridge.last_error or "")
    assert client.created == []


def test_gauge_target_opens_pr(session, editor, origin):
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"gauge": {"location": "new loc"}}',
        base='{"gauge": {"location": "old loc"}}',
        target_type=ChangeTarget.gauge,
        target_id=7,
    )
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "pr_open"
    session.refresh(bridge)
    assert bridge.state == BridgeState.pr_open
    assert "new loc" in _show(origin, f"{bridge.branch_name}:gauge.csv")
    assert "old loc" in _show(origin, "main:gauge.csv")  # base untouched


def test_processes_multiple_queued_rows(session, editor, origin):
    b1 = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "edit A"}}',
        base='{"reach": {"description": "old desc"}}',
        target_id=42,
    )
    b2 = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "edit B"}}',
        base='{"reach": {"description": "other desc"}}',
        target_id=43,
    )
    client = FakeGitHubClient()
    outcomes = worker.run_once(session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK)

    assert {o.state for o in outcomes} == {"pr_open"}
    assert len(client.created) == 2  # one PR each, distinct branches
    session.refresh(b1)
    session.refresh(b2)
    assert b1.branch_name != b2.branch_name


def test_infra_error_backs_off_quietly_below_cap(session, editor, tmp_path):
    # Clone target doesn't exist → git_ops.GitOpError (infra). Below the retry cap
    # the row stays queued, increments retry_count, and backs off (lease_expires_at
    # set to a future reclaim time) — but does NOT escalate: a transient blip that
    # may self-heal on a later run shouldn't page. Only giving up (the cap) does.
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(tmp_path / "nope.git"), clock=_CLOCK
    )

    assert outcome.escalate is False  # quiet retry, not an alert
    assert outcome.state == "queued"
    session.refresh(bridge)
    assert bridge.state == BridgeState.queued  # left for retry
    assert bridge.retry_count == 1
    assert "infrastructure error" in (bridge.last_error or "")
    assert bridge.lease_owner is None
    assert bridge.lease_expires_at is not None  # backed off (was cleared pre-change)
    assert client.created == []


def test_infra_error_parks_worker_error_at_cap_and_escalates(session, editor, tmp_path):
    # The Nth consecutive infra error (retry_count hits the cap) parks the row
    # worker_error and escalates ONCE, so a persistent outage stops retrying +
    # re-alerting every tick.
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    bridge.retry_count = worker._MAX_INFRA_RETRIES - 1  # one failure away from the cap
    session.flush()
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(tmp_path / "nope.git"), clock=_CLOCK
    )

    assert outcome.escalate is True
    assert outcome.state == "worker_error"
    session.refresh(bridge)
    assert bridge.state == BridgeState.worker_error
    assert bridge.retry_count == worker._MAX_INFRA_RETRIES
    assert "after" in (bridge.last_error or "")


def test_backed_off_row_is_skipped_until_window_passes(session, editor, tmp_path):
    # A row inside its backoff window (lease_expires_at in the future) is not
    # re-processed by a run at the same clock — run_once filters it out before it
    # can consume a `limit` slot or burn another retry.
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    client = FakeGitHubClient()
    bad_url = str(tmp_path / "nope.git")

    (first,) = worker.run_once(session, _cfg(), client=client, clone_url=bad_url, clock=_CLOCK)
    assert first.state == "queued"
    session.refresh(bridge)
    assert bridge.retry_count == 1 and bridge.lease_expires_at is not None

    # Second run at the SAME clock — still inside the backoff window → skipped.
    assert worker.run_once(session, _cfg(), client=client, clone_url=bad_url, clock=_CLOCK) == []
    session.refresh(bridge)
    assert bridge.retry_count == 1  # not retried


def test_missing_reviewed_base_fails_closed(session, editor, origin):
    bridge = _seed_request(
        session, editor.id, applied='{"reach": {"description": "new"}}', base=None
    )
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "worker_error"
    session.refresh(bridge)
    assert "no reviewed base" in (bridge.last_error or "")
    assert client.created == []  # never applied without a drift guard


def test_malformed_reviewed_base_is_worker_error(session, editor, origin):
    bridge = _seed_request(
        session, editor.id, applied='{"reach": {"description": "new"}}', base="{not json"
    )
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "worker_error"
    session.refresh(bridge)
    assert "not valid JSON" in (bridge.last_error or "")


def test_stale_resolved_parent_is_not_bridged(session, editor, origin):
    # A maintainer manually resolved the request (the documented manual path)
    # while the worker was disabled. The stale queued row must NOT open a PR.
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new desc"}}',
        base='{"reach": {"description": "old desc"}}',
        status="resolved",
    )
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "worker_error"
    assert outcome.escalate is False  # a manually-resolved parent is routine, not an alert
    session.refresh(bridge)
    assert "not approved" in (bridge.last_error or "")
    assert client.created == []  # no PR for already-completed work


def test_applied_json_changed_since_queue_fails_closed(session, editor, origin):
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new desc"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    # Tamper: the frozen diff changes after the sha was pinned at queue time.
    cr = session.get(ChangeRequest, bridge.change_request_id)
    cr.applied_json = '{"reach": {"description": "TAMPERED"}}'
    session.flush()

    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "worker_error"
    assert outcome.escalate is True  # tamper/integrity anomaly → alert
    session.refresh(bridge)
    assert "sha256" in (bridge.last_error or "")
    assert client.created == []


def test_missing_applied_json_sha_fails_closed(session, editor, origin):
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new desc"}}',
        base='{"reach": {"description": "old desc"}}',
        with_sha=False,
    )
    client = FakeGitHubClient()
    (outcome,) = worker.run_once(
        session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK
    )

    assert outcome.state == "worker_error"
    assert outcome.escalate is True
    session.refresh(bridge)
    assert "sha256" in (bridge.last_error or "")
    assert client.created == []


def test_retry_produces_identical_commit_sha(session, editor, origin):
    # The M2 property: re-processing the same row (e.g. a crash before the pr_open
    # commit) reproduces the identical commit SHA — no PR-head churn / dismissed
    # approvals — because the stamp + commit date are row-stable and the base tip
    # hasn't moved.
    bridge = _seed_request(
        session,
        editor.id,
        applied='{"reach": {"description": "new desc"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    client = FakeGitHubClient()
    worker.run_once(session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK)
    session.refresh(bridge)
    sha1 = bridge.pr_head_sha

    # Simulate a crash before the pr_open DB commit: row back to queued.
    bridge.state = BridgeState.queued
    bridge.lease_owner = None
    bridge.lease_expires_at = None
    session.flush()

    worker.run_once(session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK)
    session.refresh(bridge)
    assert bridge.pr_head_sha == sha1
    assert len(client.created) == 1  # reused the PR, didn't duplicate


def test_empty_queue_returns_nothing(session, origin):
    client = FakeGitHubClient()
    assert (
        worker.run_once(session, _cfg(), client=client, clone_url=str(origin), clock=_CLOCK) == []
    )


# ---------------------------------------------------------------------------
# reconcile (pr_open → merged / pr_closed)
# ---------------------------------------------------------------------------


def _seed_pr_open(session, editor_id, *, pr_number: int) -> ChangeRequestBridge:
    bridge = _seed_request(
        session,
        editor_id,
        applied='{"reach": {"description": "x"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    bridge.state = BridgeState.pr_open
    bridge.pr_number = pr_number
    bridge.branch_name = f"editor-proposal/{bridge.change_request_id}-1"
    session.flush()
    return bridge


def test_reconcile_marks_merged(session, editor):
    bridge = _seed_pr_open(session, editor.id, pr_number=5)
    client = FakeGitHubClient()
    client.set_pr(5, state="closed", merged=True, merge_commit_sha="abc123")
    (outcome,) = worker.reconcile(session, _cfg(), client=client)

    assert outcome.state == "merged"
    session.refresh(bridge)
    assert bridge.state == BridgeState.merged
    assert bridge.pr_merge_sha == "abc123"


def test_reconcile_marks_closed_unmerged(session, editor):
    bridge = _seed_pr_open(session, editor.id, pr_number=6)
    client = FakeGitHubClient()
    client.set_pr(6, state="closed", merged=False)
    (outcome,) = worker.reconcile(session, _cfg(), client=client)

    assert outcome.state == "pr_closed"
    session.refresh(bridge)
    assert bridge.state == BridgeState.pr_closed


def test_reconcile_leaves_open_pr_untouched(session, editor):
    bridge = _seed_pr_open(session, editor.id, pr_number=7)
    client = FakeGitHubClient()
    client.set_pr(7, state="open", merged=False)
    assert worker.reconcile(session, _cfg(), client=client) == []  # no change reported
    session.refresh(bridge)
    assert bridge.state == BridgeState.pr_open


def test_reconcile_pr_read_failure_escalates(session, editor):
    bridge = _seed_pr_open(session, editor.id, pr_number=8)
    client = FakeGitHubClient()
    client.raise_get_pr = True
    (outcome,) = worker.reconcile(session, _cfg(), client=client)

    assert outcome.escalate is True
    session.refresh(bridge)
    assert bridge.state == BridgeState.pr_open  # left for the next pass


# ---------------------------------------------------------------------------
# mark-deployed (merged → deployed + resolve parent)
# ---------------------------------------------------------------------------


def _commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """A repo with two commits A→B; returns (repo, sha_A, sha_B). A is B's ancestor."""
    import os

    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.com",
    }
    repo = tmp_path / "dataset"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "f").write_text("a", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "A"], check=True, env=env)
    sha_a = git_ops.head_sha(repo)
    (repo / "f").write_text("b", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "B"], check=True, env=env)
    sha_b = git_ops.head_sha(repo)
    return repo, sha_a, sha_b


def _seed_merged(session, editor_id, *, merge_sha: str) -> ChangeRequestBridge:
    bridge = _seed_request(
        session,
        editor_id,
        applied='{"reach": {"description": "x"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    bridge.state = BridgeState.merged
    bridge.pr_merge_sha = merge_sha
    session.flush()
    return bridge


def test_mark_deployed_exact_match_resolves_parent(session, editor, tmp_path):
    repo, _sha_a, sha_b = _commit_repo(tmp_path)
    bridge = _seed_merged(session, editor.id, merge_sha=sha_b)  # == dataset_ref
    (outcome,) = worker.mark_deployed(session, _cfg(), dataset_ref=sha_b, repo=str(repo))

    assert outcome.state == "deployed"
    session.refresh(bridge)
    assert bridge.state == BridgeState.deployed
    cr = session.get(ChangeRequest, bridge.change_request_id)
    assert cr.status == ChangeStatus.resolved
    assert "editor-bridge" in (cr.reviewer_note or "")


def test_mark_deployed_ancestor_merge_commit(session, editor, tmp_path):
    repo, sha_a, sha_b = _commit_repo(tmp_path)
    bridge = _seed_merged(session, editor.id, merge_sha=sha_a)  # A is an ancestor of B
    (outcome,) = worker.mark_deployed(session, _cfg(), dataset_ref=sha_b, repo=str(repo))

    assert outcome.state == "deployed"
    session.refresh(bridge)
    assert bridge.state == BridgeState.deployed


def test_mark_deployed_unknown_sha_left_merged(session, editor, tmp_path):
    repo, _sha_a, sha_b = _commit_repo(tmp_path)
    bridge = _seed_merged(session, editor.id, merge_sha="f" * 40)  # not in the repo
    assert worker.mark_deployed(session, _cfg(), dataset_ref=sha_b, repo=str(repo)) == []
    session.refresh(bridge)
    assert bridge.state == BridgeState.merged  # left for a later pass, not an error


def test_reconcile_merged_without_sha_waits(session, editor):
    # GitHub's async window: merged=true but merge_commit_sha not settled yet.
    # The row must stay pr_open (a merged row is never re-read), then transition
    # once the SHA appears.
    bridge = _seed_pr_open(session, editor.id, pr_number=9)
    client = FakeGitHubClient()
    client.set_pr(9, state="closed", merged=True, merge_commit_sha=None)
    assert worker.reconcile(session, _cfg(), client=client) == []
    session.refresh(bridge)
    assert bridge.state == BridgeState.pr_open  # not stranded

    client.set_pr(9, state="closed", merged=True, merge_commit_sha="settled")
    (outcome,) = worker.reconcile(session, _cfg(), client=client)
    assert outcome.state == "merged"
    session.refresh(bridge)
    assert bridge.state == BridgeState.merged
    assert bridge.pr_merge_sha == "settled"


def test_reconcile_multiple_rows_mixed(session, editor):
    b1 = _seed_pr_open(session, editor.id, pr_number=11)
    b2 = _seed_pr_open(session, editor.id, pr_number=12)
    client = FakeGitHubClient()
    client.set_pr(11, state="closed", merged=True, merge_commit_sha="m11")
    client.set_pr(12, state="closed", merged=False)
    worker.reconcile(session, _cfg(), client=client)
    session.refresh(b1)
    session.refresh(b2)
    assert b1.state == BridgeState.merged
    assert b2.state == BridgeState.pr_closed


def test_mark_deployed_skips_merged_without_sha(session, editor, tmp_path):
    repo, _sha_a, sha_b = _commit_repo(tmp_path)
    bridge = _seed_merged(session, editor.id, merge_sha=sha_b)
    bridge.pr_merge_sha = None
    session.flush()
    assert worker.mark_deployed(session, _cfg(), dataset_ref=sha_b, repo=str(repo)) == []
    session.refresh(bridge)
    assert bridge.state == BridgeState.merged


def test_mark_deployed_non_git_repo_degrades(session, editor, tmp_path):
    # repo isn't a git checkout → is_ancestor raises → row left merged (no false deploy).
    notrepo = tmp_path / "notgit"
    notrepo.mkdir()
    bridge = _seed_merged(session, editor.id, merge_sha="a" * 40)
    assert worker.mark_deployed(session, _cfg(), dataset_ref="b" * 40, repo=str(notrepo)) == []
    session.refresh(bridge)
    assert bridge.state == BridgeState.merged


def test_mark_deployed_idempotent_no_duplicate_note(session, editor, tmp_path):
    repo, _sha_a, sha_b = _commit_repo(tmp_path)
    bridge = _seed_merged(session, editor.id, merge_sha=sha_b)
    worker.mark_deployed(session, _cfg(), dataset_ref=sha_b, repo=str(repo))
    cr = session.get(ChangeRequest, bridge.change_request_id)
    note_after_first = cr.reviewer_note
    assert cr.status == ChangeStatus.resolved
    assert (note_after_first or "").count("[editor-bridge] deployed") == 1

    # A second pass (or a concurrent run) that re-sees the row as merged must not
    # append the note again — the parent is already resolved.
    bridge.state = BridgeState.merged
    session.flush()
    worker.mark_deployed(session, _cfg(), dataset_ref=sha_b, repo=str(repo))
    cr = session.get(ChangeRequest, bridge.change_request_id)
    assert cr.reviewer_note == note_after_first  # unchanged — no duplicate note


# ---------------------------------------------------------------------------
# requeue — manual recovery of a worker_error / pr_closed row
# ---------------------------------------------------------------------------


def _requeue_seed(session, editor_id, state: BridgeState) -> ChangeRequestBridge:
    bridge = _seed_request(
        session,
        editor_id,
        applied='{"reach": {"description": "new"}}',
        base='{"reach": {"description": "old desc"}}',
    )
    bridge.state = state
    bridge.attempt = 2
    bridge.retry_count = 5
    bridge.last_error = "boom"
    bridge.lease_owner = "host:1"
    session.flush()
    return bridge


def test_requeue_resets_worker_error_to_queued(session, editor):
    bridge = _requeue_seed(session, editor.id, BridgeState.worker_error)
    status, detail = worker.requeue(session, bridge.change_request_id)
    assert status == "requeued"
    assert "attempt 3" in detail
    session.refresh(bridge)
    assert bridge.state == BridgeState.queued
    assert bridge.attempt == 3  # bumped → fresh branch next run
    assert bridge.retry_count == 0
    assert bridge.last_error is None
    assert bridge.lease_owner is None and bridge.lease_expires_at is None


def test_requeue_recovers_pr_closed(session, editor):
    bridge = _requeue_seed(session, editor.id, BridgeState.pr_closed)
    assert worker.requeue(session, bridge.change_request_id)[0] == "requeued"
    session.refresh(bridge)
    assert bridge.state == BridgeState.queued


def test_requeue_refuses_conflict(session, editor):
    bridge = _requeue_seed(session, editor.id, BridgeState.conflict)
    status, detail = worker.requeue(session, bridge.change_request_id)
    assert status == "refused"
    assert "conflict" in detail
    session.refresh(bridge)
    assert bridge.state == BridgeState.conflict  # untouched — needs re-review


def test_requeue_refuses_active_queued_row(session, editor):
    bridge = _requeue_seed(session, editor.id, BridgeState.queued)
    status, _ = worker.requeue(session, bridge.change_request_id)
    assert status == "refused"


def test_requeue_refuses_done_row(session, editor):
    bridge = _requeue_seed(session, editor.id, BridgeState.deployed)
    assert worker.requeue(session, bridge.change_request_id)[0] == "refused"


def test_requeue_not_found_for_missing_row(session, editor):
    status, detail = worker.requeue(session, 999_999)
    assert status == "not_found"
    assert "999999" in detail


def test_requeue_refuses_when_parent_not_approved(session, editor):
    bridge = _requeue_seed(session, editor.id, BridgeState.worker_error)
    cr = session.get(ChangeRequest, bridge.change_request_id)
    cr.status = ChangeStatus.resolved  # manually closed since the failure
    session.flush()
    status, detail = worker.requeue(session, bridge.change_request_id)
    assert status == "refused"
    assert "not approved" in detail
    session.refresh(bridge)
    assert bridge.state == BridgeState.worker_error  # untouched


# ---------------------------------------------------------------------------
# email-on-deploy
# ---------------------------------------------------------------------------


def test_notify_proposer_deployed_emails_via_mail(session, editor, monkeypatch):
    # The real helper (captured before the autouse stub) mails the proposer their
    # change summary + review link via `mail`, and never raises.
    cr = ChangeRequest(
        target_type=ChangeTarget.reach,
        target_id=42,
        editor_id=editor.id,
        subject="Proposed edit: Wilson Reach",
        payload_json="{}",
        status=ChangeStatus.resolved,
    )
    session.add(cr)
    session.flush()

    captured: dict = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        return None

    monkeypatch.setattr(worker.shutil, "which", lambda _name: "/usr/bin/mail")
    monkeypatch.setattr(worker.subprocess, "run", fake_run)

    _REAL_NOTIFY(session, cr, "deadbeefcafe1234", _cfg("https://levels.example.org/"))

    assert captured["cmd"][0] == "mail"
    assert editor.email in captured["cmd"]  # alice@example.com
    body = captured["input"].decode("utf-8")
    assert "Wilson Reach" in body  # the proposer's own summary
    assert "deadbeefcafe" in body  # dataset commit
    assert f"/review.php?id={cr.id}" in body  # public review link, no trailing //
    assert "//review.php" not in body  # review_url's trailing slash was trimmed


def test_notify_proposer_deployed_skips_when_no_mail(session, editor, monkeypatch):
    cr = ChangeRequest(
        target_type=ChangeTarget.reach,
        target_id=42,
        editor_id=editor.id,
        subject="x",
        payload_json="{}",
        status=ChangeStatus.resolved,
    )
    session.add(cr)
    session.flush()
    monkeypatch.setattr(worker.shutil, "which", lambda _name: None)  # `mail` absent

    def boom(*a, **k):  # must NOT be called
        raise AssertionError("subprocess.run should not run without `mail`")

    monkeypatch.setattr(worker.subprocess, "run", boom)
    _REAL_NOTIFY(session, cr, "abc123", _cfg())  # no raise, no send
