"""``levels editor-bridge`` — the editor → kayak_data PR bridge worker CLI.

Subcommands (docs/PLAN_editor_pr_bridge.md):
  * ``status``        — show the bridge queue counts by state (read-only).
  * ``run-once``      — turn queued endorsements into kayak_data PRs (the worker).
  * ``queue``         — requeue a worker_error / pr_closed row as a new attempt.
  * ``reconcile``     — advance pr_open rows to merged / pr_closed (reads PR state).
  * ``mark-deployed`` — merged → deployed + resolve the parent request, once the
                        merge commit is in the deployed ``--dataset-ref``.

``run-once`` / ``reconcile`` are gated on ``editor_bridge_enabled`` + App
credentials (a host without the GitHub App provisioned is a clean no-op);
``queue`` / ``mark-deployed`` need no token (DB-only), so an operator / post-deploy
hook can call them anywhere.
"""

from __future__ import annotations

import argparse
import logging

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from kayak.config import get_config
from kayak.db.engine import get_session
from kayak.db.models import BridgeState, ChangeRequestBridge
from kayak.editor_bridge import worker

log = logging.getLogger(__name__)


def addArgs(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "editor-bridge",
        help="Editor → kayak_data PR bridge worker (open PRs for endorsed change_requests)",
    )
    sub = parser.add_subparsers(dest="bridge_command")
    parser.set_defaults(func=lambda _args: parser.print_help())

    p_status = sub.add_parser("status", help="Show the bridge queue counts by state")
    p_status.set_defaults(func=cmd_status)

    p_run = sub.add_parser("run-once", help="Process queued endorsements into kayak_data PRs")
    p_run.add_argument(
        "--limit", type=int, default=10, help="Max queued rows to process this run (default 10)"
    )
    p_run.set_defaults(func=cmd_run_once)

    p_queue = sub.add_parser(
        "queue", help="Requeue a worker_error / pr_closed bridge row as a new attempt"
    )
    p_queue.add_argument("--cr", type=int, required=True, help="change_request id to requeue")
    p_queue.set_defaults(func=cmd_queue)

    p_rec = sub.add_parser(
        "reconcile", help="Advance pr_open rows by reading PR state (merged / closed)"
    )
    p_rec.set_defaults(func=cmd_reconcile)

    p_md = sub.add_parser(
        "mark-deployed",
        help="Mark merged rows deployed when their merge commit is in --dataset-ref",
    )
    p_md.add_argument("--dataset-ref", required=True, help="The deployed dataset commit SHA")
    p_md.add_argument(
        "--dataset-repo",
        default=None,
        help="Local dataset checkout to resolve ancestry (default: DATASET_DIR)",
    )
    p_md.set_defaults(func=cmd_mark_deployed)


def cmd_status(args: argparse.Namespace) -> None:
    """Print a count of bridge rows by state."""
    session = get_session()
    try:
        rows = session.execute(
            select(ChangeRequestBridge.state, func.count())
            .group_by(ChangeRequestBridge.state)
            .order_by(ChangeRequestBridge.state)
        ).all()
    except OperationalError:
        # The bridge table isn't present (un-migrated DB) — degrade cleanly.
        print("editor-bridge: change_request_bridge table not found (run `levels migrate`)")
        return
    finally:
        session.close()
    if not rows:
        print("editor-bridge: no rows queued")
        return
    print("editor-bridge queue:")
    for state, count in rows:
        label = state.value if isinstance(state, BridgeState) else str(state)
        print(f"  {label:<12} {count}")


def cmd_run_once(args: argparse.Namespace) -> int:
    """Process queued rows into PRs. Returns non-zero only on a systemic failure.

    Per-row adapter conflicts / errors are recorded on the row (visible via
    ``status``) and logged, not escalated to a non-zero exit — a single bad
    proposal must not trip the systemd OnFailure alert chain. A systemic failure
    (missing credentials, auth/clone failure) raises out of the worker and exits
    non-zero so the operator is alerted.
    """
    cfg = get_config()
    if not cfg.editor_bridge_enabled:
        print("editor-bridge: disabled (set EDITOR_BRIDGE_ENABLED=true to run); nothing to do")
        return 0
    session = get_session()
    try:
        try:
            outcomes = worker.run_once(session, cfg, limit=args.limit)
        except worker.BridgeConfigError as exc:
            log.error("editor-bridge run-once: %s", exc)
            print(f"editor-bridge: not configured: {exc}")
            return 2
    finally:
        session.close()

    if not outcomes:
        print("editor-bridge: no queued endorsements")
        return 0
    for o in outcomes:
        suffix = f" (PR #{o.pr_number})" if o.pr_number else ""
        print(f"  cr {o.change_request_id}: {o.state}{suffix} — {o.detail}")
    pr_open = sum(1 for o in outcomes if o.state == BridgeState.pr_open.value)
    escalated = sum(1 for o in outcomes if o.escalate)
    print(f"editor-bridge: processed {len(outcomes)} row(s), {pr_open} PR(s) opened/updated")
    if escalated:
        # An infrastructure failure (row stays queued for retry) or a frozen-diff
        # integrity anomaly (row parked worker_error) — exit non-zero so the
        # systemd OnFailure chain alerts.
        print(f"editor-bridge: {escalated} row(s) need attention (infra/integrity) — see logs")
        return 1
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """Read PR state for pr_open rows; advance to merged / pr_closed."""
    cfg = get_config()
    if not cfg.editor_bridge_enabled:
        print("editor-bridge: disabled; nothing to reconcile")
        return 0
    session = get_session()
    try:
        try:
            outcomes = worker.reconcile(session, cfg)
        except worker.BridgeConfigError as exc:
            log.error("editor-bridge reconcile: %s", exc)
            print(f"editor-bridge: not configured: {exc}")
            return 2
    finally:
        session.close()

    if not outcomes:
        print("editor-bridge: no PRs to reconcile")
        return 0
    for o in outcomes:
        print(f"  cr {o.change_request_id}: {o.state} — {o.detail}")
    if any(o.escalate for o in outcomes):
        print("editor-bridge: a PR read failed (infra) — see logs")
        return 1
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    """Requeue a recoverable bridge row (worker_error / pr_closed) as a new attempt.

    Not gated on ``editor_bridge_enabled`` (a DB-only state reset, no token): an
    operator can requeue a stuck row even on a host that isn't actively bridging.
    Exits non-zero only when no bridge row exists for the id (an operator typo);
    a refusal (wrong state / parent not approved) is informational, exit 0.
    """
    session = get_session()
    try:
        status, detail = worker.requeue(session, args.cr)
    finally:
        session.close()
    print(f"editor-bridge: {detail}")
    return 1 if status == "not_found" else 0


def cmd_mark_deployed(args: argparse.Namespace) -> int:
    """Mark merged rows deployed once their merge commit is in --dataset-ref.

    Not gated on ``editor_bridge_enabled`` (no GitHub token needed): it reads
    merged bridge rows + local git ancestry and resolves the parent requests, so
    it can run as a post-deploy hook even on a host that isn't actively bridging.
    """
    cfg = get_config()
    repo = args.dataset_repo or str(cfg.dataset_dir)
    session = get_session()
    try:
        outcomes = worker.mark_deployed(session, cfg, dataset_ref=args.dataset_ref, repo=repo)
        # Distinguish "nothing merged" from "merged rows exist but none resolved"
        # (e.g. repo isn't a git checkout containing the commits — the common
        # mistake of pointing at the paired-release tar snapshot, which has no
        # .git). The latter must be visible without trawling `status`.
        still_merged = session.scalar(
            select(func.count())
            .select_from(ChangeRequestBridge)
            .where(ChangeRequestBridge.state == BridgeState.merged)
        )
    finally:
        session.close()

    if not outcomes:
        if still_merged:
            print(
                f"editor-bridge: {still_merged} merged row(s) but none resolvable in {repo} — "
                "point --dataset-repo at a git checkout fetched to the dataset ref"
            )
        else:
            print("editor-bridge: no merged rows to mark deployed")
        return 0
    for o in outcomes:
        print(f"  cr {o.change_request_id}: {o.state} — {o.detail}")
    print(f"editor-bridge: marked {len(outcomes)} row(s) deployed")
    return 0
