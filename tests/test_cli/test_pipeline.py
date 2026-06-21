"""Pipeline orchestrator: structural + branch-behavior tests.

Two flavors of test live here:

1. **Step inventory** via inspection of ``_build_steps`` — no mocking,
   asserts the ordered list of pipeline steps. Catches accidental
   reorderings / step deletions in code review.
2. **Branch behavior** — the fail-fast short-circuit (a fetch failure
   skips downstream transforms/build), the ``--continue-on-error``
   forensic-mode override, and the orphan-check soft-fail invariant
   (build runs even when orphans are present). These use mocks because
   the goal is to exercise the orchestrator's branching rather than the
   step bodies; the *integration* test (``test_pipeline_integration.py``)
   covers the end-to-end DB→HTML path.

Previously this file also held three tautological "did the mock get
called?" tests; per T2.4 in docs/done/PLAN_outstanding_followups.md / TEST-H3 they
were replaced by the inspection-based step-order assertion below.
"""

from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from kayak.cli.pipeline import _build_steps, pipeline


def _make_args(**overrides):
    defaults = {
        "skip_fetch": False,
        "dry_run": False,
        "input_dir": None,
        "output_dir": None,
        "continue_on_error": False,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


def test_pipeline_step_order():
    """Pipeline runs steps in the documented order.

    Inspects ``_build_steps`` directly — no patches. If a new step is
    added, this test must be updated to reflect the new order; that's
    the gate.
    """
    names = [step.name for step in _build_steps(skip_fetch=False)]
    assert names == [
        "fetch",
        "fetch-usgs-ogc",
        "fetch-licor",
        "calc-rating",
        "update-gauge-cache",
        "calculator",
        "build",
        "orphan-check",
        "check-reaches",
    ]


def test_pipeline_skip_fetch_drops_fetch_step():
    """``--skip-fetch`` removes only the ``fetch`` entry; nothing else moves."""
    names = [step.name for step in _build_steps(skip_fetch=True)]
    assert names == [
        "fetch-usgs-ogc",
        "fetch-licor",
        "calc-rating",
        "update-gauge-cache",
        "calculator",
        "build",
        "orphan-check",
        "check-reaches",
    ]


def test_pipeline_dag_dependencies():
    """The skip-cascade requires-graph matches the documented topology.

    Pinning this here so a typo in `_build_steps` (e.g. a missing
    requires=) doesn't silently let a downstream step run on stale
    state. Adding a new step needs one row here too — that's the
    gate.
    """
    deps = {step.name: step.requires for step in _build_steps(skip_fetch=False)}
    assert deps == {
        "fetch": (),
        "fetch-usgs-ogc": (),
        # fetch-licor has no requires AND nothing requires it: a LI-COR outage
        # neither blocks nor is blocked by the rest of the pipeline (soft step).
        "fetch-licor": (),
        "calc-rating": ("fetch", "fetch-usgs-ogc"),
        "update-gauge-cache": ("calc-rating",),
        "calculator": ("update-gauge-cache",),
        "build": ("update-gauge-cache", "calculator"),
        "orphan-check": ("build",),
        "check-reaches": ("build",),
    }


@patch("kayak.cli.pipeline._check_reaches")
@patch("kayak.cli.pipeline._orphan_check")
@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_exits_nonzero_on_failure(
    mock_fetch,
    mock_ogc,
    mock_calc_rating,
    mock_gauge_cache,
    mock_calculator,
    mock_build,
    mock_engine,
    mock_orphan_check,
    mock_check_reaches,
):
    """A fetch failure short-circuits downstream and raises SystemExit(1).

    Pre-QW.5 this test asserted "subsequent steps still ran" — the audit
    flagged that as encoding the bug as a feature (stale data baked into
    the build after fetch failed). New behavior: fail-fast. Downstream
    transform/build steps are skipped; --continue-on-error opts back into
    the old "run everything" shape (see test_pipeline_continue_on_error).
    """
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    mock_fetch.side_effect = RuntimeError("network down")

    args = _make_args()
    with pytest.raises(SystemExit) as exc_info:
        pipeline(args)
    assert exc_info.value.code == 1

    # fetch attempted; fetch-usgs-ogc is independent and still runs.
    mock_fetch.assert_called_once()
    mock_ogc.assert_called_once()
    # Downstream transforms / build short-circuit because fetch failed.
    mock_calc_rating.assert_not_called()
    mock_gauge_cache.assert_not_called()
    mock_calculator.assert_not_called()
    mock_build.assert_not_called()


@patch("kayak.cli.pipeline._check_reaches")
@patch("kayak.cli.pipeline._orphan_check")
@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_soft_fail_fetch_still_builds_but_exits_nonzero(
    mock_fetch,
    mock_ogc,
    mock_calc_rating,
    mock_gauge_cache,
    mock_calculator,
    mock_build,
    mock_engine,
    mock_orphan_check,
    mock_check_reaches,
):
    """fetch RETURNING a non-zero rc (an undeclared-station reject — S1) is a
    SOFT failure: unlike a raised exception it does NOT cascade-skip downstream,
    so build still runs and the public site never freezes on one new station —
    but the run still exits non-zero so systemd OnFailure alerts.
    """
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    mock_fetch.return_value = 1  # soft fail (return, not raise)

    args = _make_args()
    with pytest.raises(SystemExit) as exc_info:
        pipeline(args)
    assert exc_info.value.code == 1

    # The load-bearing invariant: build (and every transform) still ran.
    mock_fetch.assert_called_once()
    mock_calc_rating.assert_called_once()
    mock_gauge_cache.assert_called_once()
    mock_calculator.assert_called_once()
    mock_build.assert_called_once()


@patch("kayak.cli.pipeline._check_reaches")
@patch("kayak.cli.pipeline._orphan_check")
@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch_licor.fetch_licor")
@patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_licor_soft_fail_still_builds(
    mock_fetch,
    mock_ogc,
    mock_licor,
    mock_calc_rating,
    mock_gauge_cache,
    mock_calculator,
    mock_build,
    mock_engine,
    mock_orphan_check,
    mock_check_reaches,
):
    """fetch-licor soft-failing (config error → rc=1) alerts via non-zero exit but
    must NOT cascade-skip build — nothing lists it in `requires`, so a LI-COR
    problem never freezes the rest of the site.
    """
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    mock_licor.return_value = 1  # soft fail (config error)

    args = _make_args()
    with pytest.raises(SystemExit) as exc_info:
        pipeline(args)
    assert exc_info.value.code == 1

    # The invariant: licor soft-fail did not block the rest of the pipeline.
    mock_licor.assert_called_once()
    mock_calc_rating.assert_called_once()
    mock_build.assert_called_once()


@patch("kayak.cli.pipeline._check_reaches")
@patch("kayak.cli.pipeline._orphan_check")
@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_continue_on_error_suppresses_exit(
    mock_fetch,
    mock_ogc,
    mock_calc_rating,
    mock_gauge_cache,
    mock_calculator,
    mock_build,
    mock_engine,
    mock_orphan_check,
    mock_check_reaches,
):
    """--continue-on-error: run all steps regardless of fetch failure, exit 0.

    Forensic mode — operator wants to see how far the pipeline can get
    even with broken fetch. Disables the QW.5 fail-fast short-circuit.
    """
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    mock_fetch.side_effect = RuntimeError("network down")

    args = _make_args(continue_on_error=True)
    # Must not raise SystemExit.
    pipeline(args)

    # Every step runs even though fetch raised.
    mock_fetch.assert_called_once()
    mock_ogc.assert_called_once()
    mock_calc_rating.assert_called_once()
    mock_gauge_cache.assert_called_once()
    mock_calculator.assert_called_once()
    mock_build.assert_called_once()


@patch("kayak.cli.pipeline._check_reaches")
@patch("kayak.cli.pipeline._orphan_check")
@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_runs_downstream_when_only_usgs_ogc_fails(
    mock_fetch,
    mock_ogc,
    mock_calc_rating,
    mock_gauge_cache,
    mock_calculator,
    mock_build,
    mock_engine,
    mock_orphan_check,
    mock_check_reaches,
):
    """fetch-usgs-ogc failing also short-circuits — both fetches are 'fetch steps'."""
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    # fetch succeeds, fetch-usgs-ogc fails.
    mock_ogc.side_effect = RuntimeError("OGC endpoint down")

    args = _make_args()
    with pytest.raises(SystemExit) as exc_info:
        pipeline(args)
    assert exc_info.value.code == 1

    mock_fetch.assert_called_once()
    mock_ogc.assert_called_once()
    # Downstream still skipped — a fetch step failed.
    mock_build.assert_not_called()


@patch("kayak.cli.pipeline._check_reaches")
@patch("kayak.cli.pipeline.find_orphan_sources")
@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_orphan_check_soft_fail(
    mock_fetch,
    mock_ogc,
    mock_calc_rating,
    mock_gauge_cache,
    mock_calculator,
    mock_build,
    mock_engine,
    mock_find_orphans,
    mock_check_reaches,
):
    """Orphan-check at end of pipeline: soft-fail.

    When find_orphan_sources returns rows, the pipeline:
    - completes build (the public site stays fresh on data we have)
    - records orphan-check in the failures list
    - exits non-zero so systemd's OnFailure handler fires email + ntfy

    Asserting mock_build was called is the load-bearing soft-fail invariant
    — protects against future refactors that short-circuit before build.
    """
    from kayak.db.sources import OrphanRow

    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    mock_find_orphans.return_value = [
        OrphanRow(
            source_id=999,
            name="ORPHAN_STN",
            agency="test",
            url="https://example.com/orphan",
            is_active=True,
            latest_obs=None,
        )
    ]

    args = _make_args()
    with pytest.raises(SystemExit) as exc_info:
        pipeline(args)
    assert exc_info.value.code == 1

    # All upstream steps ran, including build (soft-fail invariant).
    mock_fetch.assert_called_once()
    mock_ogc.assert_called_once()
    mock_calc_rating.assert_called_once()
    mock_gauge_cache.assert_called_once()
    mock_calculator.assert_called_once()
    mock_build.assert_called_once()
    mock_find_orphans.assert_called_once()


@patch("kayak.cli.pipeline._check_reaches")
@patch("kayak.cli.pipeline.find_orphan_sources")
@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_orphan_check_clean_run_exits_zero(
    mock_fetch,
    mock_ogc,
    mock_calc_rating,
    mock_gauge_cache,
    mock_calculator,
    mock_build,
    mock_engine,
    mock_find_orphans,
    mock_check_reaches,
):
    """No orphans → pipeline exits 0 normally."""
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    mock_find_orphans.return_value = []

    args = _make_args()
    # Must not raise SystemExit.
    pipeline(args)
    mock_find_orphans.assert_called_once()
