"""Centralized logging setup (mirrors TPWUtils/Logger.py).

Provides Click-compatible equivalents of addArgs() and mkLogger():
- add_logging_options: decorator adding --logfile, --debug, --verbose, etc.
- setup_logging: configures the root logger from those options.
"""

from __future__ import annotations

import getpass
import logging
import logging.handlers
import socket

import click


def add_logging_options(cmd):
    """Decorator that adds TPWUtils-style logging options to a Click command/group."""
    options = [
        click.option("--logfile", type=str, default=None, metavar="FILENAME",
                      help="Name of logfile (enables rotating file handler)"),
        click.option("--logBytes", type=int, default=10_000_000, metavar="BYTES",
                      help="Maximum logfile size in bytes"),
        click.option("--logCount", type=int, default=3, metavar="COUNT",
                      help="Number of backup log files to keep"),
        click.option("--mailTo", multiple=True, metavar="ADDR",
                      help="Where to mail errors and exceptions to (repeatable)"),
        click.option("--mailFrom", type=str, default=None, metavar="ADDR",
                      help="Who the mail originates from"),
        click.option("--mailSubject", type=str, default=None, metavar="SUBJECT",
                      help="Mail subject line"),
        click.option("--smtpHost", type=str, default="localhost", metavar="HOST",
                      help="SMTP server to mail to"),
        click.option("--debug", "log_level", flag_value="DEBUG",
                      help="Enable very verbose (DEBUG) logging"),
        click.option("--verbose", "log_level", flag_value="INFO",
                      help="Enable verbose (INFO) logging"),
    ]
    for option in reversed(options):
        cmd = option(cmd)
    return cmd


def setup_logging(
    *,
    logfile: str | None = None,
    logBytes: int = 10_000_000,
    logCount: int = 3,
    mailTo: tuple[str, ...] = (),
    mailFrom: str | None = None,
    mailSubject: str | None = None,
    smtpHost: str = "localhost",
    log_level: str | None = None,
    **_kwargs,
) -> logging.Logger:
    """Configure the root logger, mirroring TPWUtils.Logger.mkLogger()."""
    logger = logging.getLogger()
    logger.handlers.clear()

    fmt = "%(asctime)s %(levelname)s: %(message)s"

    if logfile:
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=logBytes, backupCount=logCount,
        )
    else:
        handler = logging.StreamHandler()

    level = getattr(logging, log_level) if log_level else logging.WARNING
    logger.setLevel(level)
    handler.setLevel(level)

    formatter = logging.Formatter(fmt)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if mailTo:
        frm = mailFrom or (getpass.getuser() + "@" + socket.getfqdn())
        subj = mailSubject or ("Error on " + socket.getfqdn())
        smtp_handler = logging.handlers.SMTPHandler(smtpHost, frm, list(mailTo), subj)
        smtp_handler.setLevel(logging.ERROR)
        smtp_handler.setFormatter(formatter)
        logger.addHandler(smtp_handler)

    return logger
