"""Guard: a completed plan must live under docs/done/ and be indexed there.

review-4 R2.3. ``PLAN_gradient_single_source.md`` (Status: "Implemented", #42)
sat in ``docs/`` root, unindexed — the round-3 "completed plans drift out of
docs/done/" pattern. This asserts:

  1. no root ``docs/*.md`` whose Status reads Implemented/completed/done lives
     outside ``docs/done/`` — except the allowlisted ``PLAN_production_discipline.md``
     (a *landed* plan deliberately kept in ``docs/`` as a live cross-ref, per
     ``docs/done/README.md``); and
  2. every ``docs/done/PLAN_*.md`` / ``REVIEW_*.md`` is referenced in the index.

The trigger is the narrow Implemented/completed/done — *not* merged/landed/revised
— so it doesn't false-positive a ``revised``/``landed`` status, e.g. the
landed-but-kept ``PLAN_production_discipline`` deliberately retained in ``docs/``
root (see ``_ROOT_ALLOWED``).
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"
_DONE = _DOCS / "done"
_INDEX = _DONE / "README.md"

# Landed plans deliberately kept in docs/ root, reason documented in the index.
_ROOT_ALLOWED = {"PLAN_production_discipline.md"}

_DONE_STATUS = re.compile(r"\b(implemented|completed)\b|status:\s*done", re.IGNORECASE)


def _status_blob(md_text: str) -> str:
    """Text following a '## Status' heading or a '**Status:**' inline label."""
    m = re.search(r"(?:^##\s*Status\s*$\s*|\**Status:\**)(.*)", md_text, re.MULTILINE)
    return m.group(1) if m else ""


def test_completed_plans_live_under_docs_done() -> None:
    offenders = [
        md.name
        for md in sorted(_DOCS.glob("*.md"))  # docs/ root only; docs/done/ excluded
        if md.name not in _ROOT_ALLOWED and _DONE_STATUS.search(_status_blob(md.read_text("utf-8")))
    ]
    assert not offenders, (
        "completed plans found in docs/ root — move to docs/done/ and index them, "
        "or add to _ROOT_ALLOWED with a reason:\n  " + "\n  ".join(offenders)
    )


def test_docs_done_plans_are_indexed() -> None:
    index = _INDEX.read_text("utf-8")
    missing = [
        p.name
        for p in sorted(_DONE.glob("PLAN_*.md")) + sorted(_DONE.glob("REVIEW_*.md"))
        if f"`{p.name}`" not in index
    ]
    assert not missing, "docs/done/ plans not referenced in docs/done/README.md:\n  " + "\n  ".join(
        missing
    )


def test_status_trigger_distinguishes_done_from_in_progress() -> None:
    """The trigger must fire on a completed plan and ignore in-progress/landed —
    so the main test isn't vacuous and won't false-positive a Revised/landed status."""
    assert _DONE_STATUS.search(_status_blob("## Status\nImplemented in this PR.\n"))
    assert _DONE_STATUS.search(_status_blob("## Status\n\nImplemented after a blank line.\n"))
    assert not _DONE_STATUS.search(_status_blob("**Status:** Revised 2026-05-19\n"))
    assert not _DONE_STATUS.search(_status_blob("**Status:** landed 2026-05-15\n"))
