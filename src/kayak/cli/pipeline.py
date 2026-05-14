"""Pipeline orchestrator (replaces scripts/master).

Runs the full data pipeline in order:
1. fetch — fetch from remote agencies
2. fetch-usgs-ogc — fetch USGS data via OGC API
3. calc-rating — apply rating tables
4. update-gauge-cache — recompute gauge-level latest values
5. calculator — compute derived values
6. build — generate output pages
7. orphan-check — soft-fail if any fetch-active source lacks a gauge_source link
"""

import argparse
import logging
import time
from collections.abc import Callable

from sqlalchemy import text

from kayak.cli import build, calc_rating, calculator, fetch, fetch_usgs_ogc
from kayak.db.engine import get_engine
from kayak.db.sources import find_orphan_sources

logger = logging.getLogger(__name__)

# Steps that acquire new data. If any of these fail, running the downstream
# transform/build steps just bakes stale data into the next published HTML
# without any signal to the operator. Fail-fast skips them so the
# OnFailure=kayak-notify-failure hook fires loudly instead.
_FETCH_STEP_NAMES = frozenset({"fetch", "fetch-usgs-ogc"})


def _skip_downstream_after_fetch_failure(
    step_name: str,
    failures: list[tuple[str, str]],
    continue_on_error: bool,
) -> bool:
    """Decide whether to short-circuit a non-fetch step when a fetch failed."""
    if continue_on_error:
        return False
    if step_name in _FETCH_STEP_NAMES:
        return False
    return any(f[0] in _FETCH_STEP_NAMES for f in failures)


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
    try/except appends to its ``failures`` list and the run exits non-zero
    — at which point systemd marks ``kayak-pipeline.service`` failed and
    fires its existing ``OnFailure=kayak-notify-failure@%n.service`` chain
    (email + ntfy). See ``docs/PLAN_orphan_sources.md`` Phase 2b.
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


_Step = tuple[str, "Callable[[argparse.Namespace], None]"]


def _build_steps(skip_fetch: bool) -> list[_Step]:
    """Return the ordered list of pipeline steps.

    Pulled out so tests can assert step order by inspection (no mocking).
    Honors --skip-fetch by dropping the `fetch` step.
    """
    steps: list[_Step] = []
    if not skip_fetch:
        steps.append(("fetch", fetch.fetch))
    steps.append(("fetch-usgs-ogc", fetch_usgs_ogc.fetch_usgs_ogc))
    steps.extend(
        [
            ("calc-rating", calc_rating.calc_rating),
            ("update-gauge-cache", _update_gauge_cache),
            ("calculator", calculator.calculator),
            ("build", build.build),
            ("orphan-check", _orphan_check),
        ]
    )
    return steps


def pipeline(args: argparse.Namespace) -> None:
    """Run the full data pipeline."""
    steps = _build_steps(args.skip_fetch)

    failures: list[tuple[str, str]] = []
    skipped: list[str] = []

    for step_name, func in steps:
        # Fail-fast: if any fetch step already failed, skip downstream transforms
        # and build so we don't publish stale data silently. --continue-on-error
        # opts out of this for forensic runs.
        if _skip_downstream_after_fetch_failure(step_name, failures, args.continue_on_error):
            print(f"\n{'=' * 60}", flush=True)
            print(f"Skipping: {step_name} (upstream fetch step failed)", flush=True)
            print(f"{'=' * 60}", flush=True)
            skipped.append(step_name)
            continue

        print(f"\n{'=' * 60}", flush=True)
        print(f"Running: {step_name}", flush=True)
        print(f"{'=' * 60}", flush=True)
        start = time.time()
        try:
            func(args)
        except SystemExit:
            pass
        except Exception as e:
            logger.error("Error in %s: %s", step_name, e)
            failures.append((step_name, str(e)))
        elapsed = time.time() - start
        print(f"Completed {step_name} in {elapsed:.1f}s", flush=True)

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
            f"Pipeline skipped {len(skipped)} step(s) due to upstream fetch failure: "
            f"{', '.join(skipped)}",
            flush=True,
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
