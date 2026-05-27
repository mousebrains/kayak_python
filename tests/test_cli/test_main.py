"""Tests for kayak.cli.main entry point."""

import sys
from unittest import mock

import pytest

from kayak.cli.main import main


def test_version_flag(capsys):
    """--version prints the installed package version and exits."""
    from importlib.metadata import version

    with (
        mock.patch.object(sys, "argv", ["levels", "--version"]),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert f"levels {version('kayak')}" in captured.out


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
