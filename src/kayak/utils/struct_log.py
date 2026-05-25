"""Structured event logging for the pipeline + scheduled scripts.

Each call to :func:`emit` writes one JSON object on a single line via
the dedicated ``kayak.events`` logger. Under systemd that lands in
``journald`` as a bare-JSON ``MESSAGE=`` (no timestamp/level prefix);
``journalctl --output=json`` preserves the envelope verbatim, and the
recap script (`scripts/recap.py`) re-parses those lines into a run
summary.

``kayak.events`` is configured here, independently of the human root
logger that :func:`kayak.cli.logger.mkLogger` sets up — and that
independence is load-bearing, not incidental:

- **Pinned at INFO.** ``mkLogger`` leaves the root logger at WARNING
  unless ``--verbose``/``--debug`` is passed, and the pipeline runs as
  a plain ``levels pipeline`` (no flag). Without its own level the
  events logger would inherit WARNING and every INFO event would be
  dropped before reaching journald — recap then reports
  "Events parsed: 0".
- **Its own bare ``%(message)s`` handler.** The root handler's
  ``"%(asctime)s %(levelname)s: %(message)s"`` format would prepend a
  timestamp + level, so the journald ``MESSAGE`` would no longer start
  with ``{`` and recap's ``startswith("{")`` parse gate would skip it.
- **``propagate`` left on.** The record still reaches the root logger,
  but the root handler's WARNING level filters out the INFO duplicate
  in production. Tests (and any future root-level aggregation) that
  capture via the root logger therefore still see the record.

Schema:
- ``event`` — short identifier (e.g. ``"step_done"``)
- ``ts``   — Unix epoch seconds at emission time (float)
- ``run_id`` — optional; if set on the parent caller, lets recap
  group events from the same pipeline run. The pipeline orchestrator
  passes the same ``run_id`` to every step call.
- everything else is event-specific (step name, elapsed seconds,
  error string, etc.)

Keep the helper small. Adding more shape (severity, source file, etc.)
makes downstream consumers brittle.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


def _events_logger() -> logging.Logger:
    """Return the dedicated ``kayak.events`` logger, configured once.

    Idempotent: a repeat import reuses the module-level singleton, and
    the handler guard keeps a second call from stacking duplicate lines
    into journald. See the module docstring for why the level, handler,
    and propagation are set the way they are.
    """
    lg = logging.getLogger("kayak.events")
    lg.setLevel(logging.INFO)
    lg.propagate = True
    if not lg.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        # Bare JSON only — recap.py keys on MESSAGE starting with '{'.
        handler.setFormatter(logging.Formatter("%(message)s"))
        lg.addHandler(handler)
    return lg


_logger = _events_logger()


def emit(event: str, **fields: Any) -> None:
    """Log one structured event line.

    ``fields`` must be JSON-serializable; non-serializable values
    fall back to ``str()`` via ``json.dumps(default=str)`` so a typo
    on the caller side doesn't crash the pipeline mid-run.
    """
    payload = {"event": event, "ts": time.time(), **fields}
    _logger.info(json.dumps(payload, default=str))
