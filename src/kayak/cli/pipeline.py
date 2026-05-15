"""Pipeline orchestrator (replaces scripts/master).

Runs the data pipeline as a DAG with explicit per-step ``requires``.
Steps are still evaluated in topological list order (no parallelism
yet); ``requires`` controls only the skip cascade — a step is skipped
when any of its prerequisites failed or was itself skipped. That
collapses the old ``_FETCH_STEP_NAMES`` hard-coded fail-fast into the
step list itself, so adding a new step is one row, not a code edit
across two helpers.

Default DAG:

  fetch ─────────┐
  fetch-usgs-ogc ┤
                 ├──> calc-rating ──> update-gauge-cache ──> calculator ──> build ──> orphan-check
"""

import argparse
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import text

from kayak.cli import build, calc_rating, calculator, fetch, fetch_usgs_ogc
from kayak.db.engine import get_engine
from kayak.db.sources import find_orphan_sources
from kayak.utils.struct_log import emit as struct_emit

logger = logging.getLogger(__name__)


class _Result(Enum):
    """Per-step outcome tracked across pipeline iteration."""

    ok = "ok"
    failed = "failed"
    skipped = "skipped"


@dataclass(frozen=True)
class _Step:
    name: str
    fn: Callable[[argparse.Namespace], None]
    # Names of steps that must have completed (ok) for this step to run.
    # A step is skipped when any of its prerequisites failed OR was
    # skipped, modulo --continue-on-error (which disables the cascade).
    # An entry naming a step that wasn't scheduled (e.g. "fetch" under
    # --skip-fetch) is treated as a no-op — the missing prereq doesn't
    # block this step. That preserves the long-standing "--skip-fetch
    # lets the rest of the pipeline run" contract.
    requires: tuple[str, ...] = field(default_factory=tuple)


def _should_skip(
    step: _Step,
    results: dict[str, _Result],
    continue_on_error: bool,
) -> bool:
    """Skip this step when any required upstream failed or was skipped."""
    if continue_on_error:
        return False
    for req in step.requires:
        outcome = results.get(req)
        if outcome in (_Result.failed, _Result.skipped):
            return True
    return False


def addArgs(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Register the 'pipeline' subcommand."""
    parser = subparsers.add_parser("pipeline", help="Run the full data pipeline")
    parser.set_defaults(func=pipeline)
    parser.add_argument("--skip-fetch", action="store_true", help="Skip the fetch step")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep running subsequent steps after a failure (default: stop-at-end exit code)",
    )

    # Include all fetch options (dry-run, input-dir, etc.)
    fetch.addArgs_options(parser)


def _update_gauge_cache(args: argparse.Namespace) -> None:
    """Recompute gauge-level latest observation cache."""
    from kayak.db.cache import update_all_latest_gauges
    from kayak.db.engine import get_session

    session = get_session()
    try:
        update_all_latest_gauges(session)
        print("Gauge cache updated")
    finally:
        session.close()


def _orphan_check(args: argparse.Namespace) -> None:
    """Soft-fail if any fetch-active source has no gauge_source link.

    Runs *after* build so a fresh orphan never blocks the public site from
    updating. Raises ``RuntimeError`` so the pipeline's existing per-step
    try/except records the failure and the run exits non-zero — at which
    point systemd marks ``kayak-pipeline.service`` failed and fires its
    existing ``OnFailure=kayak-notify-failure@%n.service`` chain (email
    + ntfy). See ``docs/done/PLAN_orphan_sources.md`` Phase 2b.
    """
    from kayak.db.engine import get_session

    session = get_session()
    try:
        rows = find_orphan_sources(session)
    finally:
        session.close()

    if not rows:
        print("Orphan-check: clean.")
        return

    logger.error("Orphan-check found %d unlinked fetch-active source(s):", len(rows))
    for r in rows:
        logger.error(
            "  source.id=%d name=%s url=%s latest=%s",
            r.source_id,
            r.name,
            r.url,
            r.latest_obs.isoformat() if r.latest_obs else "(none)",
        )
    raise RuntimeError(f"{len(rows)} orphan source(s) found — see ERROR logs above")


def _build_steps(skip_fetch: bool) -> list[_Step]:
    """Return the ordered list of pipeline steps.

    Pulled out so tests can assert structure by inspection (no mocking).
    ``--skip-fetch`` drops the ``fetch`` step; downstream steps name
    ``"fetch"`` in ``requires`` but the missing-prereq rule treats that
    as a no-op so the rest of the pipeline still runs.
    """
    steps: list[_Step] = []
    if not skip_fetch:
        steps.append(_Step("fetch", fetch.fetch))
    steps.append(_Step("fetch-usgs-ogc", fetch_usgs_ogc.fetch_usgs_ogc))
    steps.extend(
        [
            _Step(
                "calc-rating",
                calc_rating.calc_rating,
                requires=("fetch", "fetch-usgs-ogc"),
            ),
            _Step("update-gauge-cache", _update_gauge_cache, requires=("calc-rating",)),
            _Step("calculator", calculator.calculator, requires=("update-gauge-cache",)),
            _Step("build", build.build, requires=("update-gauge-cache", "calculator")),
            _Step("orphan-check", _orphan_check, requires=("build",)),
        ]
    )
    return steps


def pipeline(args: argparse.Namespace) -> None:
    """Run the full data pipeline."""
    steps = _build_steps(args.skip_fetch)
    # Random run_id so the recap script can group all step events from
    # a single pipeline invocation, even when several runs interleave
    # in journald (manual + systemd-triggered).
    run_id = secrets.token_hex(6)
    pipeline_start = time.time()

    struct_emit(
        "pipeline_start",
        run_id=run_id,
        steps=[s.name for s in steps],
        skip_fetch=bool(args.skip_fetch),
        continue_on_error=bool(args.continue_on_error),
    )

    results: dict[str, _Result] = {}
    failures: list[tuple[str, str]] = []
    skipped: list[str] = []

    for step in steps:
        if _should_skip(step, results, args.continue_on_error):
            print(f"\n{'=' * 60}", flush=True)
            print(f"Skipping: {step.name} (upstream prerequisite failed)", flush=True)
            print(f"{'=' * 60}", flush=True)
            results[step.name] = _Result.skipped
            skipped.append(step.name)
            struct_emit(
                "step_skipped",
                run_id=run_id,
                step=step.name,
                reason="upstream_failed",
            )
            continue

        print(f"\n{'=' * 60}", flush=True)
        print(f"Running: {step.name}", flush=True)
        print(f"{'=' * 60}", flush=True)
        struct_emit("step_start", run_id=run_id, step=step.name)
        start = time.time()
        try:
            step.fn(args)
            results[step.name] = _Result.ok
        except SystemExit:
            results[step.name] = _Result.ok
        except Exception as e:
            logger.error("Error in %s: %s", step.name, e)
            failures.append((step.name, str(e)))
            results[step.name] = _Result.failed
        elapsed = time.time() - start
        print(f"Completed {step.name} in {elapsed:.1f}s", flush=True)
        struct_emit(
            "step_done" if results[step.name] is _Result.ok else "step_failed",
            run_id=run_id,
            step=step.name,
            elapsed_s=round(elapsed, 3),
            outcome=results[step.name].value,
            error=failures[-1][1] if results[step.name] is _Result.failed else None,
        )

    # Run PRAGMA optimize to update SQLite query planner statistics
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("PRAGMA optimize"))
            conn.commit()
    except Exception as e:
        logger.warning("PRAGMA optimize failed: %s", e)

    print(f"\n{'=' * 60}", flush=True)
    if skipped:
        print(
            f"Pipeline skipped {len(skipped)} step(s) due to upstream failure: "
            f"{', '.join(skipped)}",
            flush=True,
        )
    struct_emit(
        "pipeline_done",
        run_id=run_id,
        elapsed_s=round(time.time() - pipeline_start, 3),
        ok=sum(1 for r in results.values() if r is _Result.ok),
        failed=len(failures),
        skipped=len(skipped),
    )
    if failures:
        print(f"Pipeline finished with {len(failures)} failure(s):", flush=True)
        for step_name, msg in failures:
            print(f"  - {step_name}: {msg}", flush=True)
        if not args.continue_on_error:
            raise SystemExit(1)
        print("(exit code suppressed by --continue-on-error)", flush=True)
        return
    print("Pipeline complete", flush=True)
