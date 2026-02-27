"""Tests for kayak.cli.logger argument helpers and logger configuration."""

import argparse
import logging

from kayak.cli.logger import addArgs, mkLogger


def _parse_with_logger(*cli_args):
    """Build an ArgumentParser with logger args and parse the given CLI tokens."""
    parser = argparse.ArgumentParser()
    addArgs(parser)
    return parser.parse_args(list(cli_args))


def test_addArgs_adds_expected_arguments():
    """addArgs registers --logfile, --debug, --verbose and related flags."""
    parser = argparse.ArgumentParser()
    addArgs(parser)
    # Parse empty args to confirm no required arguments were added
    ns = parser.parse_args([])
    assert hasattr(ns, "logfile")
    assert hasattr(ns, "log_level")
    assert hasattr(ns, "logBytes")
    assert hasattr(ns, "logCount")


def test_mkLogger_default_warning(caplog):
    """mkLogger with no level flags sets WARNING level on root logger."""
    args = _parse_with_logger()
    logger = mkLogger(args)
    assert logger.level == logging.WARNING


def test_mkLogger_debug_flag():
    """mkLogger with --debug sets DEBUG level."""
    args = _parse_with_logger("--debug")
    logger = mkLogger(args)
    assert logger.level == logging.DEBUG


def test_mkLogger_verbose_flag():
    """mkLogger with --verbose sets INFO level."""
    args = _parse_with_logger("--verbose")
    logger = mkLogger(args)
    assert logger.level == logging.INFO


def test_mkLogger_logfile_creates_file_handler(tmp_path):
    """mkLogger with --logfile attaches a RotatingFileHandler."""
    log_file = tmp_path / "test.log"
    args = _parse_with_logger("--logfile", str(log_file))
    logger = mkLogger(args)

    handler_types = [type(h).__name__ for h in logger.handlers]
    assert "RotatingFileHandler" in handler_types
