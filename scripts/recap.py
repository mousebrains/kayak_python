#!/usr/bin/env python3
"""Summarize the last N days of pipeline activity from journald.

Reads structured-event lines emitted by ``kayak.utils.struct_log`` —
JSON envelopes lifted out of journald's ``MESSAGE=`` field — and
produces a step-level run summary plus a short failure tally. Drives
PLAN_outstanding_followups.md §6.1's "last 30 days recap" deliverable.

Default: 30 days, all ``kayak-*`` units. Override either via flags.
Healthchecks.io history integration is intentionally out of scope for
this first cut — when journald has the data, that's the source of
truth.

Run manually:
    python3 scripts/recap.py
    python3 scripts/recap.py --days 7 --unit kayak-pipeline.service
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from typing import Any

# Events emitted by kayak.utils.struct_log. Anything else with an
# ``event`` field (e.g. ntfy.sh's response bodies, which carry
# ``"event": "message"``) is not ours and gets dropped.
_KNOWN_EVENTS = frozenset(
    {
        "pipeline_start",
        "pipeline_done",
        "step_start",
        "step_done",
        "step_failed",
        "step_skipped",
    }
)


def _journalctl_json(unit_glob: str, since: str) -> list[dict[str, Any]]:
    """Pull journald entries for the given unit glob and time window.

    ``unit_glob`` is fed to ``--unit``; ``journalctl`` accepts globs
    directly (``kayak-*``).  Returns the list of entries with our
    JSON-envelope MESSAGE successfully parsed, dropping anything else
    silently — the recap only cares about structured events.
    """
    cmd = [
        "journalctl",
        "--unit",
        unit_glob,
        "--since",
        since,
        "--output",
        "json",
        "--no-pager",
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        print("error: journalctl not available on this host", file=sys.stderr)
        sys.exit(2)
    except subprocess.CalledProcessError as e:
        print(f"error: journalctl failed: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(2)

    events: list[dict[str, Any]] = []
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        message = entry.get("MESSAGE")
        if not isinstance(message, str) or not message.startswith("{"):
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("event") not in _KNOWN_EVENTS:
            continue
        # Carry the systemd unit forward so we can group cross-unit.
        payload["_unit"] = entry.get("_SYSTEMD_UNIT") or entry.get("UNIT")
        events.append(payload)
    return events


def _bucket_events(
    events: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    Counter,
    dict[str, str],
    dict[str, list[float]],
]:
    """Sort raw events into four buckets the formatter wants."""
    runs: dict[str, dict[str, Any]] = defaultdict(dict)
    step_outcome: Counter = Counter()
    step_failures: dict[str, str] = {}
    step_elapsed: dict[str, list[float]] = defaultdict(list)

    for ev in events:
        et = ev.get("event")
        run_id = ev.get("run_id")
        if et == "pipeline_start" and run_id:
            runs[run_id]["start_ts"] = ev.get("ts")
            runs[run_id]["steps"] = ev.get("steps") or []
        elif et == "pipeline_done" and run_id:
            for key in ("elapsed_s", "ok", "failed", "skipped"):
                runs[run_id][key] = ev.get(key, 0 if key != "elapsed_s" else None)
        elif et == "step_done":
            step_outcome[(ev.get("step"), "ok")] += 1
            if (e := ev.get("elapsed_s")) is not None:
                step_elapsed[ev.get("step", "?")].append(float(e))
        elif et == "step_failed":
            step_outcome[(ev.get("step"), "failed")] += 1
            if err := ev.get("error"):
                step_failures[ev.get("step", "?")] = str(err)
        elif et == "step_skipped":
            step_outcome[(ev.get("step"), "skipped")] += 1

    return runs, step_outcome, step_failures, step_elapsed


def _summarize(events: list[dict[str, Any]]) -> str:
    """Render a plain-text summary table.

    Buckets:
    - pipeline runs (start/done pairs, by run_id)
    - per-step outcomes (ok / failed / skipped)
    - failure tally with one example error per failing step
    """
    runs, step_outcome, step_failures, step_elapsed = _bucket_events(events)

    lines: list[str] = [f"Pipeline runs: {len(runs)}"]
    completed = [r for r in runs.values() if "elapsed_s" in r and r.get("elapsed_s") is not None]
    if completed:
        elapsed_vals = sorted(r["elapsed_s"] for r in completed)
        lines.append(
            "  elapsed_s (s) — "
            f"min {elapsed_vals[0]:.1f}, "
            f"med {elapsed_vals[len(elapsed_vals) // 2]:.1f}, "
            f"max {elapsed_vals[-1]:.1f}"
        )
        ok = sum(r.get("ok", 0) for r in completed)
        failed = sum(r.get("failed", 0) for r in completed)
        skipped = sum(r.get("skipped", 0) for r in completed)
        lines.append(f"  step totals — ok {ok}, failed {failed}, skipped {skipped}")

    if step_outcome:
        lines.append("")
        lines.append("Per-step outcomes:")
        for s in sorted({s for s, _ in step_outcome}):
            ok = step_outcome[(s, "ok")]
            failed = step_outcome[(s, "failed")]
            skipped = step_outcome[(s, "skipped")]
            elapsed = step_elapsed.get(s) or []
            tail = ""
            if elapsed:
                tail = (
                    f"  (elapsed_s med {sorted(elapsed)[len(elapsed) // 2]:.1f}, "
                    f"max {max(elapsed):.1f})"
                )
            lines.append(f"  {s:<22} ok {ok:>4}  failed {failed:>3}  skipped {skipped:>3}{tail}")

    if step_failures:
        lines.append("")
        lines.append("Most recent failure per step:")
        for step, err in sorted(step_failures.items()):
            lines.append(f"  {step}: {err}")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="Look-back window (default: 30)")
    ap.add_argument(
        "--unit",
        default="kayak-*",
        help="systemd unit glob to scan (default: kayak-*)",
    )
    args = ap.parse_args(argv)

    events = _journalctl_json(args.unit, since=f"{args.days} days ago")
    print(f"Recap window: last {args.days} day(s), unit={args.unit}")
    print(f"Events parsed: {len(events)}")
    print()
    print(_summarize(events))
    return 0


if __name__ == "__main__":
    sys.exit(main())
