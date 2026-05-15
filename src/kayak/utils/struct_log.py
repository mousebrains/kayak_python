"""Structured event logging for the pipeline + scheduled scripts.

Each call to :func:`emit` writes one JSON object on a single line at
INFO level via the ``kayak.events`` logger. Under systemd that lands
in ``journald`` as ``MESSAGE=<json>``; ``journalctl --output=json``
preserves the envelope verbatim. The recap script (`scripts/recap.py`)
re-parses these lines to produce a 30-day run summary.

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
import time
from typing import Any

_logger = logging.getLogger("kayak.events")


def emit(event: str, **fields: Any) -> None:
    """Log one structured event line.

    ``fields`` must be JSON-serializable; non-serializable values
    fall back to ``str()`` via ``json.dumps(default=str)`` so a typo
    on the caller side doesn't crash the pipeline mid-run.
    """
    payload = {"event": event, "ts": time.time(), **fields}
    _logger.info(json.dumps(payload, default=str))
