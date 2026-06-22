"""The editor → kayak_data PR bridge worker (Tier 4 of docs/PLAN_editor_pr_bridge.md).

For each endorsed change_request the PHP layer queued (a ``change_request_bridge``
row in state ``queued``), this opens exactly one ``kayak_data`` PR:

    lease → clone the dataset repo → apply the Tier 3 adapter (with the reviewed
    base as the drift guard) → commit + push a deterministic proposal branch →
    open or reuse the PR → record ``pr_open`` (or ``conflict`` / ``worker_error``).

It never merges, never pushes the base branch, and holds only a short-lived App
installation token (minted per run). Branch protection requiring a human
approving review is the merge gate — the App's bot author can't self-approve.

**Idempotent under crashes.** The branch name is deterministic
(``<prefix><change_request_id>-<attempt>``) and the PR is discovered-before-create,
so a crash after the push but before the DB update simply re-clones, re-applies
(same diff), re-pushes (force, same content), finds the existing PR, and updates
it — never a duplicate. The reach stamp + commit dates derive from a row-stable
timestamp, so a retry produces identical *content* and an identical commit *SHA*
(the SHA is identical as long as the base-branch tip hasn't advanced — it's only
re-processed while still ``queued``, i.e. before the PR is stably open, so no
already-open PR head is ever churned). A per-row lease keeps two overlapping
``run-once`` calls from double-acting a row.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from kayak.config import KayakConfig
from kayak.db.models import (
    BridgeState,
    ChangeRequest,
    ChangeRequestBridge,
    ChangeStatus,
    Editor,
)
from kayak.editor_bridge import dataset_patch, git_ops, github_app, github_client

log = logging.getLogger(__name__)

# Git author for the bridge's commits. The push is authenticated as the GitHub
# App (that's what GitHub attributes + authorizes), so this is only the local
# author/committer shown in `git log`; keep it a clear non-human identity.
_BOT_NAME = "kayak editor-bridge"
_BOT_EMAIL = "editor-bridge@users.noreply.github.com"

# How long a claimed row stays leased to this worker before another run may
# reclaim it (covers a crash mid-row). A row never stays ``queued`` after a
# successful process() — every path sets a terminal-ish state — so this only
# matters for an interrupted run.
_LEASE_TTL = _dt.timedelta(minutes=15)

# Infra-error retry policy. A transient clone/push/REST/auth failure leaves the
# row queued but backed off (lease_expires_at = now + backoff, which _claim/run-once
# already honour as the earliest-reclaim time) instead of being hammered every
# tick. After _MAX_INFRA_RETRIES consecutive failures the row is parked
# worker_error and escalates ONCE — so a persistent outage alerts on give-up, not
# on every transient retry. A maintainer requeues it once the cause is fixed.
_MAX_INFRA_RETRIES = 5
_RETRY_BACKOFF_BASE = _dt.timedelta(minutes=5)
_RETRY_BACKOFF_CAP = _dt.timedelta(hours=1)


def _retry_backoff(retry_count: int) -> _dt.timedelta:
    """Exponential backoff for the *retry_count*-th (1-based) infra retry.

    5, 10, 20, 40 min … capped at _RETRY_BACKOFF_CAP. The cap keeps a long outage
    from pushing the next retry days out once retry_count grows.
    """
    secs = _RETRY_BACKOFF_BASE.total_seconds() * (2 ** max(0, retry_count - 1))
    return min(_dt.timedelta(seconds=secs), _RETRY_BACKOFF_CAP)


# The dataset's updated_at CSV format (e.g. "2026-04-22 23:33:10") — match it so
# the reach stamp the adapter writes diffs cleanly against the existing column.
_STAMP_FMT = "%Y-%m-%d %H:%M:%S"

Clock = Callable[[], _dt.datetime]


@dataclass(frozen=True)
class RowOutcome:
    """What the worker did with one queued bridge row.

    ``escalate`` marks an outcome the operator should be *alerted* to — an
    *infrastructure* failure (clone/push/REST/auth or an unexpected exception) or
    a frozen-diff *integrity* anomaly (applied_json sha mismatch, which should
    never happen on a legit row) — as opposed to a routine per-proposal data
    outcome (conflict, adapter rejection, no-op, superseded parent). The CLI exits
    non-zero when any outcome escalates, tripping the systemd alert chain; a single
    bad/expected proposal does not.
    """

    bridge_id: int
    change_request_id: int
    state: str
    detail: str
    pr_number: int | None = None
    pr_url: str | None = None
    escalate: bool = False


class BridgeConfigError(RuntimeError):
    """The bridge isn't configured to run (missing App credentials, etc.)."""


def run_once(
    session: Session,
    cfg: KayakConfig,
    *,
    client: github_client.GitHubClient | None = None,
    clone_url: str | None = None,
    token: str | None = None,
    clock: Clock | None = None,
    limit: int = 10,
) -> list[RowOutcome]:
    """Process up to *limit* queued bridge rows; return one outcome per row acted on.

    Production callers pass nothing extra: the worker mints an App installation
    token, builds a REST client, and derives the HTTPS clone URL from config.
    Tests inject *client* (a fake), *clone_url* (a local bare repo), and
    *token* (``None`` — a local push needs no auth), which skips minting.
    """
    clk: Clock = clock or (lambda: _dt.datetime.now(_dt.UTC))
    now = clk()
    # Skip rows still inside their backoff/lease window (lease_expires_at in the
    # future) — they'd fail _claim anyway, and excluding them keeps a backed-off
    # row from consuming a `limit` slot that a ready row could use. Mirrors
    # _claim's gate.
    rows = list(
        session.scalars(
            select(ChangeRequestBridge)
            .where(
                ChangeRequestBridge.state == BridgeState.queued,
                or_(
                    ChangeRequestBridge.lease_expires_at.is_(None),
                    ChangeRequestBridge.lease_expires_at < now,
                ),
            )
            .order_by(ChangeRequestBridge.queued_at)
            .limit(limit)
        )
    )
    if not rows:
        return []

    owner = cfg.editor_bridge_dataset_owner
    name = cfg.editor_bridge_dataset_name
    if client is None:
        token = _mint_token(cfg)
        client = github_client.RestGitHubClient(owner=owner, repo=name, token=token)
        clone_url = clone_url or f"https://github.com/{owner}/{name}.git"
    if clone_url is None:
        raise BridgeConfigError("no clone_url and no client to derive one from")

    lease_owner = f"{socket.gethostname()}:{os.getpid()}"
    outcomes: list[RowOutcome] = []
    for bridge in rows:
        if not _claim(session, bridge, lease_owner, clk):
            log.info("bridge row %s already leased; skipping", bridge.id)
            continue
        outcomes.append(
            _process_row(
                session, cfg, bridge, client=client, clone_url=clone_url, token=token, clock=clk
            )
        )
    return outcomes


def reconcile(
    session: Session,
    cfg: KayakConfig,
    *,
    client: github_client.GitHubClient | None = None,
    limit: int = 50,
) -> list[RowOutcome]:
    """Advance ``pr_open`` rows by reading each PR's current state on GitHub.

    ``pr_open`` → ``merged`` (recording ``pr_merge_sha``) when the PR merged, or
    → ``pr_closed`` when it was closed unmerged. A still-open PR is left as is. A
    REST error reading one PR is an infra failure: the row stays ``pr_open`` and
    the outcome escalates (so the run exits non-zero), the rest still reconcile.
    """
    rows = list(
        session.scalars(
            select(ChangeRequestBridge)
            .where(ChangeRequestBridge.state == BridgeState.pr_open)
            .limit(limit)
        )
    )
    if not rows:
        return []
    if client is None:
        client = github_client.RestGitHubClient(
            owner=cfg.editor_bridge_dataset_owner,
            repo=cfg.editor_bridge_dataset_name,
            token=_mint_token(cfg),
        )
    outcomes: list[RowOutcome] = []
    for bridge in rows:
        if bridge.pr_number is None:  # shouldn't happen for pr_open
            outcomes.append(
                _terminal(session, bridge, BridgeState.worker_error, "pr_open row has no pr_number")
            )
            continue
        try:
            pr = client.get_pr(bridge.pr_number)
        except github_client.GitHubApiError as exc:
            log.error("reconcile: PR #%s read failed: %s", bridge.pr_number, exc)
            outcomes.append(
                RowOutcome(
                    bridge.id, bridge.change_request_id, bridge.state.value, str(exc), escalate=True
                )
            )
            continue
        if pr.merged and pr.merge_commit_sha:
            bridge.state = BridgeState.merged
            bridge.pr_merge_sha = pr.merge_commit_sha
            session.commit()
            outcomes.append(
                RowOutcome(
                    bridge.id, bridge.change_request_id, BridgeState.merged.value, "PR merged"
                )
            )
        elif pr.merged:
            # GitHub reports `merged` before `merge_commit_sha` settles (async merge
            # window). Don't transition yet — a merged row is never re-read by
            # reconcile, so capturing a null SHA here would strand it forever
            # (mark-deployed needs the SHA). Leave it pr_open for the next pass.
            log.info("reconcile: PR #%s merged but merge SHA not yet settled", bridge.pr_number)
        elif pr.state == "closed":  # closed without merging
            bridge.state = BridgeState.pr_closed
            session.commit()
            outcomes.append(
                RowOutcome(
                    bridge.id,
                    bridge.change_request_id,
                    BridgeState.pr_closed.value,
                    "PR closed unmerged",
                )
            )
        # else: still open — leave as pr_open, no outcome row
    return outcomes


def mark_deployed(
    session: Session,
    cfg: KayakConfig,
    *,
    dataset_ref: str,
    repo: str | Path,
    limit: int = 100,
) -> list[RowOutcome]:
    """Mark ``merged`` rows ``deployed`` once their merge commit is in *dataset_ref*.

    A row's ``pr_merge_sha`` counts as deployed when it is *dataset_ref* itself or
    an ancestor of it (so several PRs merged since the last deploy all resolve from
    one deploy). *repo* must be a local **git checkout** of the dataset containing
    both commits (the deploy syncs it). On ``deployed``, the parent
    ``change_request`` advances to ``resolved`` with a note — closing the SA-lite
    loop. A row whose merge commit isn't in *repo* (or whose SHA isn't recorded
    yet) is left ``merged`` for the next pass — never marked deployed unverified.
    """
    rows = list(
        session.scalars(
            select(ChangeRequestBridge)
            .where(ChangeRequestBridge.state == BridgeState.merged)
            .limit(limit)
        )
    )
    outcomes: list[RowOutcome] = []
    for bridge in rows:
        merge_sha = bridge.pr_merge_sha
        if not merge_sha:  # merged without a recorded SHA (reconcile waits for it) — skip
            continue
        try:
            # Always via is_ancestor (reflexive: a commit is its own ancestor) so the
            # exact-match case still *proves the commit is present in repo* — a bare
            # string `==` would mark a row deployed against a checkout that never
            # contained the commit. is_ancestor raises (→ skip) on an absent SHA.
            deployed = git_ops.is_ancestor(repo, merge_sha, dataset_ref)
        except git_ops.GitOpError as exc:
            # The merge commit isn't in this checkout yet (or git errored) — not
            # deployed as far as we can tell; leave it merged for a later pass.
            log.info("mark-deployed: %s not yet resolvable in repo: %s", merge_sha[:12], exc)
            continue
        if not deployed:
            continue
        bridge.state = BridgeState.deployed
        _resolve_parent(session, bridge, dataset_ref, cfg)
        session.commit()
        outcomes.append(
            RowOutcome(bridge.id, bridge.change_request_id, BridgeState.deployed.value, "deployed")
        )
    return outcomes


# States a requeue can recover: the worker either never opened a PR
# (worker_error) or its PR was closed unmerged (pr_closed). conflict is excluded
# — a drifted dataset needs a fresh review, not a blind re-attempt on a stale base.
_REQUEUEABLE = frozenset({BridgeState.worker_error, BridgeState.pr_closed})


def requeue(session: Session, cr_id: int) -> tuple[str, str]:
    """Reset a recoverable bridge row to ``queued`` as a new attempt.

    Returns ``(status, detail)``: ``"requeued"`` (reset to queued), ``"refused"``
    (wrong state — already active/done, a conflict that needs re-review, or a
    parent no longer approved), or ``"not_found"`` (no bridge row for this id).

    Bumps ``attempt`` so the next run pushes a fresh branch / opens a fresh PR
    (a closed PR can't be reopened on the same branch), clears retry_count + lease
    + error + conflict, and REUSES the captured reviewed_base — still valid for the
    recoverable states, since nothing the worker did changed the dataset.
    """
    bridge = session.scalar(
        select(ChangeRequestBridge).where(ChangeRequestBridge.change_request_id == cr_id)
    )
    if bridge is None:
        return ("not_found", f"no bridge row for change_request {cr_id}")

    cr = session.get(ChangeRequest, cr_id)
    if cr is None or cr.status != ChangeStatus.approved:
        status = cr.status.value if cr is not None else "missing"
        return ("refused", f"cr {cr_id} parent is {status}, not approved — not requeued")

    if bridge.state == BridgeState.conflict:
        return (
            "refused",
            f"cr {cr_id} is in conflict (dataset moved since review) — re-review the "
            "change via the editor; requeue would reuse a now-stale base",
        )
    if bridge.state not in _REQUEUEABLE:
        return (
            "refused",
            f"cr {cr_id} is {bridge.state.value}; only worker_error / pr_closed rows requeue",
        )

    bridge.attempt += 1
    bridge.state = BridgeState.queued
    bridge.retry_count = 0
    bridge.last_error = None
    bridge.conflict_json = None
    bridge.lease_owner = None
    bridge.lease_expires_at = None
    session.commit()
    log.info("bridge row %s (cr %s) requeued as attempt %s", bridge.id, cr_id, bridge.attempt)
    return ("requeued", f"cr {cr_id} requeued as attempt {bridge.attempt}")


def _resolve_parent(
    session: Session, bridge: ChangeRequestBridge, dataset_ref: str, cfg: KayakConfig
) -> None:
    """Advance the parent change_request to resolved (the SA-lite loop closer).

    Idempotent: a missing or already-``resolved`` parent is a no-op, so a second
    pass (or two overlapping mark-deployed runs, until a lease lands) can't append
    the deploy note twice — and the proposer is emailed exactly once, on the real
    transition.
    """
    cr = session.get(ChangeRequest, bridge.change_request_id)
    if cr is None or cr.status == ChangeStatus.resolved:
        return
    cr.status = ChangeStatus.resolved
    note = f"[editor-bridge] deployed in dataset {dataset_ref[:12]}"
    cr.reviewer_note = f"{cr.reviewer_note}\n\n{note}" if cr.reviewer_note else note
    _notify_proposer_deployed(session, cr, dataset_ref, cfg)


def _notify_proposer_deployed(
    session: Session, cr: ChangeRequest, dataset_ref: str, cfg: KayakConfig
) -> None:
    """Best-effort 'your change is live' email to the original proposer.

    Never raises: mark-deployed runs as a post-activation deploy hook, and the
    parent is already resolved in the same transaction — a mail failure must not
    fail the deploy (the reconciler/state is unaffected). Carries only the
    proposer's own change summary + the public review link; no maintainer private
    notes or credentials (the PR-data exposure rule). Mirrors the audit digest's
    ``mail`` subprocess path; a host without ``mail`` simply logs and skips.
    """
    editor = session.get(Editor, cr.editor_id) if cr.editor_id is not None else None
    if editor is None or not editor.email:
        return
    if shutil.which("mail") is None:
        log.warning("editor-bridge: 'mail' not on PATH; skipping deploy notice for cr %s", cr.id)
        return
    summary = cr.subject or f"change request {cr.id}"
    subject = f"Your kayak change is live: {summary}"
    lines = [
        f"Your proposed change ({summary}) has been deployed to the live site.",
        f"Dataset commit: {dataset_ref[:12]}",
    ]
    if cfg.editor_bridge_review_url:
        base_url = str(cfg.editor_bridge_review_url).rstrip("/")
        lines.append(f"Details: {base_url}/review.php?id={cr.id}")
    body = "\n".join(lines) + "\n"
    try:
        subprocess.run(
            ["mail", "-s", subject, editor.email],
            input=body.encode("utf-8"),
            check=True,
            timeout=30,
        )
        log.info("editor-bridge: emailed deploy notice to the proposer of cr %s", cr.id)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        log.warning("editor-bridge: failed to email deploy notice for cr %s: %s", cr.id, exc)


def _mint_token(cfg: KayakConfig) -> str:
    if (
        cfg.editor_bridge_app_id is None
        or cfg.editor_bridge_app_installation_id is None
        or cfg.editor_bridge_app_key_path is None
    ):
        raise BridgeConfigError(
            "editor-bridge worker needs EDITOR_BRIDGE_APP_ID, "
            "EDITOR_BRIDGE_APP_INSTALLATION_ID, and EDITOR_BRIDGE_APP_KEY_PATH"
        )
    pem = github_app.load_private_key(cfg.editor_bridge_app_key_path)
    return github_app.mint_installation_token(
        app_id=cfg.editor_bridge_app_id,
        installation_id=cfg.editor_bridge_app_installation_id,
        private_key_pem=pem,
        repository=cfg.editor_bridge_dataset_name,
    ).token


def _claim(session: Session, bridge: ChangeRequestBridge, lease_owner: str, clock: Clock) -> bool:
    """CAS-claim a queued row so overlapping runs don't double-act it.

    The conditional UPDATE only matches a still-``queued`` row whose lease is
    unset/expired; we then re-read and confirm *we* hold it. (Checking the
    refreshed lease owner avoids depending on the driver's ``rowcount``.)
    """
    now = clock()
    session.execute(
        update(ChangeRequestBridge)
        .where(
            ChangeRequestBridge.id == bridge.id,
            ChangeRequestBridge.state == BridgeState.queued,
            or_(
                ChangeRequestBridge.lease_expires_at.is_(None),
                ChangeRequestBridge.lease_expires_at < now,
            ),
        )
        .values(lease_owner=lease_owner, lease_expires_at=now + _LEASE_TTL, heartbeat_at=now)
    )
    session.commit()
    session.refresh(bridge)
    return bridge.state == BridgeState.queued and bridge.lease_owner == lease_owner


@dataclass
class _Prepared:
    """The validated, ready-to-apply inputs for one bridge row."""

    cr: ChangeRequest
    target_id: int
    target_type: str
    applied: dict
    expected_base: dict
    updated_at: str
    commit_date: str


def _prepare(session: Session, bridge: ChangeRequestBridge, clock: Clock) -> _Prepared | RowOutcome:
    """Load + validate a row's change_request, diff, and drift base.

    Returns a :class:`_Prepared` on success, or a terminal :class:`RowOutcome`
    (already recorded) for any unworkable row — so :func:`_process_row` stays a
    straight-line orchestration.
    """
    cr = session.get(ChangeRequest, bridge.change_request_id)
    if cr is None:  # CASCADE should prevent this, but never act blind
        return _terminal(session, bridge, BridgeState.worker_error, "change_request row is gone")
    if cr.status != ChangeStatus.approved:
        # The parent is no longer active work — a maintainer may have manually
        # marked it resolved (or it was rejected) via the documented manual path,
        # which doesn't touch change_request_bridge. Stale queued rows can exist
        # because #215 queues while this worker is still disabled. Do NOT open a PR
        # for already-completed/closed work; retire the row instead.
        return _terminal(
            session,
            bridge,
            BridgeState.worker_error,
            f"parent change_request is '{cr.status}', not approved — superseded, not bridging",
        )
    if cr.target_id is None:
        return _terminal(
            session, bridge, BridgeState.worker_error, "change_request has no target_id"
        )
    # Tamper/edit guard: the worker must commit exactly the diff that was queued +
    # reviewed. If applied_json changed since queue time (so its sha differs from
    # the pinned applied_json_sha256), the reviewed base could still match the
    # dataset yet we'd push a DIFFERENT, unreviewed value — fail closed.
    expected_sha = bridge.applied_json_sha256
    actual_sha = hashlib.sha256((cr.applied_json or "").encode("utf-8")).hexdigest()
    if not expected_sha or actual_sha != expected_sha:
        # This should never happen on a legitimate row (applied_json is frozen at
        # endorse, and Tier 2 pins its sha in the same transaction), so it's an
        # anomaly — a post-queue mutation of the reviewed diff or a bug — not a
        # routine data outcome. Escalate so it surfaces as an alert, not just in
        # `status`.
        return _terminal(
            session,
            bridge,
            BridgeState.worker_error,
            "applied_json sha256 missing or changed since queueing — not bridging",
            escalate=True,
        )
    tt = str(cr.target_type)
    try:
        applied = json.loads(cr.applied_json or "{}")
    except ValueError:
        return _terminal(
            session, bridge, BridgeState.worker_error, "applied_json is not valid JSON"
        )
    try:
        expected_base = _expected_base(bridge, tt)
    except ValueError:
        return _terminal(
            session, bridge, BridgeState.worker_error, "reviewed_base_json is not valid JSON"
        )
    if expected_base is None:
        # Fail closed: a queued row must carry the reviewed base for the fields it
        # changes, or the worker would apply with NO drift guard. (Tier 2 always
        # captures it; a row without one is suspect.)
        return _terminal(
            session,
            bridge,
            BridgeState.worker_error,
            f"no reviewed base captured for {tt}; refusing to apply without a drift guard",
        )
    # Stamp the reach updated_at + the commit dates from a ROW-STABLE timestamp
    # (the endorse time), not wall-clock now — so a retry of the same proposal
    # produces byte-identical content AND an identical commit SHA. A clock-based
    # stamp would change the file + the SHA on every retry, churning the PR head
    # and dismissing any human review approval already given.
    stamp_dt = cr.reviewed_at or cr.submitted_at or clock()
    updated_at = stamp_dt.strftime(_STAMP_FMT)
    return _Prepared(
        cr, cr.target_id, tt, applied, expected_base, updated_at, f"{updated_at} +0000"
    )


def _process_row(
    session: Session,
    cfg: KayakConfig,
    bridge: ChangeRequestBridge,
    *,
    client: github_client.GitHubClient,
    clone_url: str,
    token: str | None,
    clock: Clock,
) -> RowOutcome:
    prepared = _prepare(session, bridge, clock)
    if isinstance(prepared, RowOutcome):
        return prepared
    cr = prepared.cr

    branch = f"{cfg.editor_bridge_branch_prefix}{cr.id}-{bridge.attempt}"
    workdir = Path(tempfile.mkdtemp(prefix="kayak-bridge-"))
    try:
        git_ops.clone(
            clone_url, workdir, token=token, branch=cfg.editor_bridge_base_branch, depth=1
        )
        git_ops.checkout_branch(workdir, branch, start_point="HEAD")
        try:
            results = dataset_patch.apply_change(
                workdir,
                prepared.target_type,
                prepared.target_id,
                prepared.applied,
                updated_at=prepared.updated_at,
                expected_base=prepared.expected_base,
            )
        except dataset_patch.ConflictError as exc:
            return _terminal(
                session,
                bridge,
                BridgeState.conflict,
                str(exc),
                conflict_json=json.dumps({"reason": str(exc)}),
            )
        except dataset_patch.DatasetPatchError as exc:
            return _terminal(session, bridge, BridgeState.worker_error, f"adapter rejected: {exc}")

        changed = [r for r in results if not r.is_noop]
        if not changed:
            return _terminal(
                session,
                bridge,
                BridgeState.worker_error,
                "no-op: dataset already matches the proposal",
            )

        git_ops.stage(workdir, [r.file for r in changed])
        head = git_ops.commit(
            workdir,
            _commit_message(cr, changed),
            author_name=_BOT_NAME,
            author_email=_BOT_EMAIL,
            date=prepared.commit_date,
        )
        git_ops.push(workdir, branch=branch, remote_url=clone_url, token=token, force=True)
        pr = _open_or_update_pr(client, cfg, cr, branch)
    except (git_ops.GitOpError, github_client.GitHubApiError, github_app.GitHubAuthError) as exc:
        # Infrastructure failure (transient or systemic). Count it; once the count
        # reaches the cap, park the row worker_error and escalate ONCE so a
        # persistent outage stops retrying (and re-alerting) every tick — a
        # maintainer requeues it after fixing the cause.
        bridge.retry_count += 1
        if bridge.retry_count >= _MAX_INFRA_RETRIES:
            return _terminal(
                session,
                bridge,
                BridgeState.worker_error,
                f"infrastructure error after {bridge.retry_count} attempts: {exc}",
                escalate=True,
            )
        # Below the cap: leave the row QUEUED but back off — set lease_expires_at to
        # the earliest-reclaim time (the same field _claim/run-once gate on), so the
        # row isn't hammered every tick. Quiet (no escalate): a transient blip that
        # self-heals on a later run shouldn't page; only the give-up above does.
        backoff = _retry_backoff(bridge.retry_count)
        bridge.last_error = (
            f"infrastructure error (retry {bridge.retry_count}, backoff {backoff}): {exc}"
        )
        bridge.lease_owner = None
        bridge.lease_expires_at = clock() + backoff
        session.commit()
        log.error(
            "bridge row %s infra error (retry %s, backoff %s, left queued): %s",
            bridge.id,
            bridge.retry_count,
            backoff,
            exc,
        )
        return RowOutcome(bridge.id, cr.id, bridge.state.value, str(exc))
    except Exception as exc:  # last resort: never leave a silent poison pill
        # An unclassified error is a bug, not a transient: park it as worker_error
        # (so it isn't retried forever) but escalate so it's noticed.
        return _terminal(
            session, bridge, BridgeState.worker_error, f"unexpected error: {exc}", escalate=True
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    bridge.state = BridgeState.pr_open
    bridge.branch_name = branch
    bridge.pr_number = pr.number
    bridge.pr_url = pr.html_url
    bridge.pr_head_sha = head
    bridge.last_error = None
    bridge.conflict_json = None  # a prior attempt's conflict no longer applies
    bridge.retry_count = 0  # cleared on success: count is per-uninterrupted-attempt
    bridge.lease_owner = None
    bridge.lease_expires_at = None
    session.commit()
    log.info("bridge row %s → pr_open #%s (%s)", bridge.id, pr.number, pr.html_url)
    return RowOutcome(
        bridge.id, cr.id, BridgeState.pr_open.value, "pr opened", pr.number, pr.html_url
    )


def _expected_base(bridge: ChangeRequestBridge, target_type: str) -> dict | None:
    """The reviewed drift base for *target_type*, or None if none is captured.

    Returns None when ``reviewed_base_json`` is absent or has no entry for this
    table. NOTE: the worker (``_prepare``) treats that None as **fail-closed**
    (``worker_error`` — never apply without a drift guard); only the lower-level
    ``dataset_patch.apply_change`` would interpret a None ``expected_base`` as
    "skip the drift check," and the worker deliberately never threads None
    through to it. Raises ValueError if ``reviewed_base_json`` is present but
    unparseable / not an object.
    """
    if not bridge.reviewed_base_json:
        return None
    base = json.loads(bridge.reviewed_base_json)  # ValueError on malformed JSON
    if not isinstance(base, dict):
        raise ValueError("reviewed_base_json is not an object")
    sub = base.get(target_type)
    return sub if isinstance(sub, dict) else None


def _open_or_update_pr(
    client: github_client.GitHubClient, cfg: KayakConfig, cr: ChangeRequest, branch: str
) -> github_client.PullRequest:
    title = _pr_title(cr)
    body = _pr_body(cfg, cr)
    existing = client.find_open_pr(branch, base_branch=cfg.editor_bridge_base_branch)
    if existing is not None:
        return client.update_pr(existing.number, title=title, body=body)
    return client.create_pr(
        head_branch=branch, base_branch=cfg.editor_bridge_base_branch, title=title, body=body
    )


def _pr_title(cr: ChangeRequest) -> str:
    return f"Editor proposal: {cr.target_type} {cr.target_id} (change_request {cr.id})"


def _pr_body(cfg: KayakConfig, cr: ChangeRequest) -> str:
    """Public PR body — no proposer email or private maintainer notes.

    Links to the authenticated review page (where the full context lives) and
    states the bot/merge-gate contract.
    """
    lines = [
        "Automated proposal from the editor → kayak_data bridge.",
        "",
        f"- target: `{cr.target_type}` id `{cr.target_id}`",
        f"- change_request: `{cr.id}`",
    ]
    if cfg.editor_bridge_review_url is not None:
        base = str(cfg.editor_bridge_review_url).rstrip("/")
        lines.append(f"- review: {base}/review.php?id={cr.id}")
    lines += [
        "",
        "Endorsed by a maintainer in the web editor and frozen for data review. "
        "Validate via the dataset CI, then a human reviewer approves + merges "
        "(this bot cannot merge its own PR). Mark the request resolved once it deploys.",
    ]
    return "\n".join(lines)


def _commit_message(cr: ChangeRequest, changed: list[dataset_patch.PatchResult]) -> str:
    files = ", ".join(sorted({r.file for r in changed}))
    return (
        f"editor-bridge: {cr.target_type} {cr.target_id} (change_request {cr.id})\n\n"
        f"Endorsed editor proposal applied to {files}. See the linked review page."
    )


def _terminal(
    session: Session,
    bridge: ChangeRequestBridge,
    state: BridgeState,
    detail: str,
    *,
    conflict_json: str | None = None,
    escalate: bool = False,
) -> RowOutcome:
    """Record a terminal-ish outcome (conflict / worker_error) + return it.

    Releases the lease (so a later requeue isn't starved) and sets/clears
    ``conflict_json`` to match the new state (a non-conflict outcome must not keep
    a stale conflict snapshot from an earlier attempt).
    """
    bridge.state = state
    bridge.last_error = detail
    bridge.conflict_json = conflict_json
    bridge.lease_owner = None
    bridge.lease_expires_at = None
    session.commit()
    log.warning("bridge row %s → %s: %s", bridge.id, state.value, detail)
    return RowOutcome(bridge.id, bridge.change_request_id, state.value, detail, escalate=escalate)
