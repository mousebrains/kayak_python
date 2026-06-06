"""Tests for kayak.cli.main entry point."""

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from kayak.cli.main import main


def test_version_flag(capsys):
    """--version echoes the package version (kayak.__version__) and exits."""
    from kayak import __version__

    with (
        mock.patch.object(sys, "argv", ["levels", "--version"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert f"levels {__version__}" in captured.out


def test_detect_version_fallback(monkeypatch):
    """_detect_version returns the sentinel when no installed dist is found."""
    from importlib.metadata import PackageNotFoundError

    import kayak

    def _raise(_name):
        raise PackageNotFoundError(_name)

    monkeypatch.setattr(kayak, "version", _raise)
    assert kayak._detect_version() == "0+unknown"


def test_no_args_exits_with_error(capsys):
    """Calling main with no arguments prints help and exits with code 1."""
    with mock.patch.object(sys, "argv", ["levels"]), pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_help_flag(capsys):
    """--help prints usage information and exits cleanly."""
    with (
        mock.patch.object(sys, "argv", ["levels", "--help"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out.lower() or "levels" in captured.out.lower()


def test_known_subcommands_registered():
    """All expected subcommands are wired into the argument parser."""
    expected = {"init-db", "fetch", "calc-rating", "calculator", "build", "pipeline"}
    # Build the parser the same way main() does, but inspect rather than execute
    import argparse

    from kayak.cli import build, calc_rating, calculator, fetch, init_db, pipeline
    from kayak.cli.logger import addArgs as addLoggerArgs

    parser = argparse.ArgumentParser(prog="levels")
    addLoggerArgs(parser)
    subparsers = parser.add_subparsers(dest="command")

    init_db.addArgs(subparsers)
    fetch.addArgs(subparsers)
    calc_rating.addArgs(subparsers)
    calculator.addArgs(subparsers)
    build.addArgs(subparsers)
    pipeline.addArgs(subparsers)

    # _subparsers is a list of _SubParsersAction; choices holds the names
    registered = set()
    for action in parser._subparsers._actions:
        if hasattr(action, "choices") and action.choices:
            registered.update(action.choices.keys())

    assert expected.issubset(registered)


def test_unknown_subcommand_exits():
    """An unrecognised subcommand causes argparse to exit with an error."""
    with (
        mock.patch.object(sys, "argv", ["levels", "bogus-cmd"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    # argparse exits with code 2 for unrecognised arguments
    assert exc_info.value.code in (1, 2)


def test_module_invocation_runs_cli():
    """`python -m kayak.cli.main …` runs the CLI (not a silent exit-0 no-op).

    The ``if __name__ == "__main__"`` guard matters because S4b will wire
    ``validate-dataset`` into kayak_data's CI; a vacuous exit-0 spelling would be
    a footgun. Uses sys.executable + PYTHONPATH=src so it's robust to whether
    the package is installed.
    """
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "dataset"
    src = Path(__file__).resolve().parents[2] / "src"
    env = {**os.environ, "PYTHONPATH": str(src)}
    result = subprocess.run(
        [sys.executable, "-m", "kayak.cli.main", "validate-dataset", str(fixture)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "validation OK" in result.stdout  # proves it actually validated
