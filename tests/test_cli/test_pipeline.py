"""Smoke tests for the pipeline orchestrator."""

from argparse import Namespace
from unittest.mock import MagicMock, patch

from kayak.cli.pipeline import pipeline


def _make_args(**overrides):
    defaults = {
        "skip_fetch": False,
        "dry_run": False,
        "input_dir": None,
        "output_dir": None,
    }
    defaults.update(overrides)
    return Namespace(**defaults)


@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_calls_all_steps(
    mock_fetch, mock_calc_rating, mock_gauge_cache, mock_calculator, mock_build, mock_engine
):
    """Pipeline calls fetch → calc-rating → update-gauge-cache → calculator → build in order."""
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    args = _make_args()
    pipeline(args)

    mock_fetch.assert_called_once_with(args)
    mock_calc_rating.assert_called_once_with(args)
    mock_gauge_cache.assert_called_once_with(args)
    mock_calculator.assert_called_once_with(args)
    mock_build.assert_called_once_with(args)


@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_skip_fetch(
    mock_fetch, mock_calc_rating, mock_gauge_cache, mock_calculator, mock_build, mock_engine
):
    """--skip-fetch omits the fetch step."""
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    args = _make_args(skip_fetch=True)
    pipeline(args)

    mock_fetch.assert_not_called()
    mock_calc_rating.assert_called_once()
    mock_gauge_cache.assert_called_once()
    mock_calculator.assert_called_once()
    mock_build.assert_called_once()


@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_pragma_optimize(
    mock_fetch, mock_calc_rating, mock_gauge_cache, mock_calculator, mock_build, mock_engine
):
    """Pipeline runs PRAGMA optimize after all steps."""
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    args = _make_args()
    pipeline(args)

    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "optimize" in str(sql).lower()


@patch("kayak.cli.pipeline.get_engine")
@patch("kayak.cli.pipeline.build.build")
@patch("kayak.cli.pipeline.calculator.calculator")
@patch("kayak.cli.pipeline._update_gauge_cache")
@patch("kayak.cli.pipeline.calc_rating.calc_rating")
@patch("kayak.cli.pipeline.fetch.fetch")
def test_pipeline_step_error_continues(
    mock_fetch, mock_calc_rating, mock_gauge_cache, mock_calculator, mock_build, mock_engine
):
    """An exception in one step does not stop subsequent steps."""
    conn = MagicMock()
    mock_engine.return_value.connect.return_value.__enter__ = MagicMock(return_value=conn)
    mock_engine.return_value.connect.return_value.__exit__ = MagicMock(return_value=False)

    mock_fetch.side_effect = RuntimeError("network down")

    args = _make_args()
    pipeline(args)

    mock_fetch.assert_called_once()
    mock_calc_rating.assert_called_once()
    mock_build.assert_called_once()
