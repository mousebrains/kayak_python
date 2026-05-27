"""Guard: CHANGELOG [Unreleased] must not describe a shipped review-ID as open.

review-4 R2.2. The CHANGELOG's Security bullet said "a residual … is tracked as
R1.5 in the round-3 plan" *after* #48 had closed R1.5 — a closed security item
shown as still-tracked (the round-3 "doc/ops hygiene lags every merge" thesis).
This asserts no review-ID recorded shipped (an ``R<n>.<n>`` co-occurring with a
``#<pr>`` reference in an archived ``docs/done/PLAN_*.md``) is described in the
CHANGELOG's ``[Unreleased]`` section alongside an open-status word.

It mechanizes only the closed-ID-as-open class — the highest-trust fact, per the
plan; the other CHANGELOG facts (baseline count, coverage floor) stay manual. A
*completeness* check would fight the file's stated "curated and thematic" policy.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CHANGELOG = _ROOT / "CHANGELOG.md"
_DONE = _ROOT / "docs" / "done"

_RID = re.compile(r"\bR\d+\.\d+\b")
_PR = re.compile(r"#\d+")
_OPEN_WORD = re.compile(
    r"\b(tracked|residual|unresolved|pending|still|to-?do|open)\b", re.IGNORECASE
)


def _shipped_review_ids() -> set[str]:
    """Review-IDs recorded shipped — an ``R<n>.<n>`` on a line that also carries a
    ``#<pr>`` reference — across the archived plans in ``docs/done/``."""
    shipped: set[str] = set()
    for plan in sorted(_DONE.glob("PLAN_*.md")):
        for line in plan.read_text(encoding="utf-8").splitlines():
            if _PR.search(line):
                shipped.update(_RID.findall(line))
    return shipped


def _unreleased_section() -> str:
    text = _CHANGELOG.read_text(encoding="utf-8")
    m = re.search(r"##\s*\[Unreleased\](.*?)(?:\n##\s|\Z)", text, re.DOTALL)
    return m.group(1) if m else ""


def _find_offenders(section: str, shipped: set[str]) -> list[str]:
    """Lines in ``section`` that name a shipped review-ID next to an open-status word."""
    out: list[str] = []
    for line in section.splitlines():
        if not _OPEN_WORD.search(line):
            continue
        out += [f"{rid}: {line.strip()}" for rid in _RID.findall(line) if rid in shipped]
    return out


def test_changelog_unreleased_no_shipped_id_marked_open() -> None:
    shipped = _shipped_review_ids()
    assert shipped, "no shipped review-IDs found in docs/done — parser regressed?"
    offenders = _find_offenders(_unreleased_section(), shipped)
    assert not offenders, (
        "CHANGELOG [Unreleased] describes shipped review-IDs as open/tracked "
        "(they shipped per docs/done/PLAN_*.md — fix the wording):\n  " + "\n  ".join(offenders)
    )


def test_guard_detects_a_shipped_id_described_as_open() -> None:
    """The detector must actually fire — the live [Unreleased] currently has no
    review-IDs, so without this the main test could pass vacuously if the regexes
    regressed. Proves the R1.5-as-tracked drift is caught, and a clean shipped
    mention is not."""
    assert _find_offenders("A residual is tracked as R1.5 in the round-3 plan.", {"R1.5"})
    assert _find_offenders("R1.5 shipped in #48.", {"R1.5"}) == []
