"""Centralized logging setup (mirrors TPWUtils/Logger.py).

Provides argparse-compatible equivalents of TPWUtils addArgs()/mkLogger():
- addArgs: adds --logfile, --debug, --verbose, etc. to an ArgumentParser
- mkLogger: configures the root logger from the parsed args namespace.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import logging.handlers
import socket


def addArgs(parser: argparse.ArgumentParser) -> None:
    """Add TPWUtils-style logging options to an ArgumentParser."""
    parser.add_argument(
        "--logfile", default=None, metavar="FILENAME",
        help="Name of logfile (enables rotating file handler)",
    )
    parser.add_argument(
        "--logBytes", type=int, default=10_000_000, metavar="BYTES",
        help="Maximum logfile size in bytes",
    )
    parser.add_argument(
        "--logCount", type=int, default=3, metavar="COUNT",
        help="Number of backup log files to keep",
    )
    parser.add_argument(
        "--mailTo", action="append", default=[], metavar="ADDR",
        help="Where to mail errors and exceptions to (repeatable)",
    )
    parser.add_argument(
        "--mailFrom", default=None, metavar="ADDR",
        help="Who the mail originates from",
    )
    parser.add_argument(
        "--mailSubject", default=None, metavar="SUBJECT",
        help="Mail subject line",
    )
    parser.add_argument(
        "--smtpHost", default="localhost", metavar="HOST",
        help="SMTP server to mail to",
    )

    level_group = parser.add_mutually_exclusive_group()
    level_group.add_argument(
        "--debug", action="store_const", const="DEBUG", dest="log_level",
        help="Enable very verbose (DEBUG) logging",
    )
    level_group.add_argument(
        "--verbose", action="store_const", const="INFO", dest="log_level",
        help="Enable verbose (INFO) logging",
    )


def mkLogger(args: argparse.Namespace) -> logging.Logger:
    """Configure the root logger, mirroring TPWUtils.Logger.mkLogger()."""
    logger = logging.getLogger()
    logger.handlers.clear()

    fmt = "%(asctime)s %(levelname)s: %(message)s"

    if args.logfile:
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            args.logfile, maxBytes=args.logBytes, backupCount=args.logCount,
        )
    else:
        handler = logging.StreamHandler()

    level = getattr(logging, args.log_level) if args.log_level else logging.WARNING
    logger.setLevel(level)
    handler.setLevel(level)

    formatter = logging.Formatter(fmt)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if args.mailTo:
        frm = args.mailFrom or (getpass.getuser() + "@" + socket.getfqdn())
        subj = args.mailSubject or ("Error on " + socket.getfqdn())
        smtp_handler = logging.handlers.SMTPHandler(
            args.smtpHost, frm, list(args.mailTo), subj,
        )
        smtp_handler.setLevel(logging.ERROR)
        smtp_handler.setFormatter(formatter)
        logger.addHandler(smtp_handler)

    return logger
