"""Guard (round-5 R2.1): every mechanically-checkable Verify in an archived
remediation plan must still pass against HEAD — so no plan can record a fix as
done while the source disagrees.

The round-5 headline was that round-4's R1.1/R1.2/R1.3 were archived as "shipped"
(R1.1's own Verify said ``grep -c 'DELETE FROM pages' scripts/db_push.sh`` → ``0``)
yet never landed in the repo. This test mechanizes exactly that class: it parses
the ``**Verify:**`` fields of ``docs/done/PLAN_round*_remediation.md`` and runs the
``grep -c '<literal>' <path> → <N>`` subset against HEAD. Prose Verifies stay
manual — covered by the "each fix ships its own committed test" rule. Full
rationale + the convergence trail: ``project-review-5/PLAN_round5_remediation.md``.

Self-reference notes (a plan *about* this parser is itself a parsed input once
archived): the field regex anchors on a BARE ``**Verify:**`` so backtick-wrapped
prose mentions don't open spurious fields, captures the field as a DOTALL block so
a wrapped continuation-line command isn't dropped, and counter (b) flags only a
``grep -c`` span that carries a quote (a real command attempt, not a prose mention).

A plan carrying a whole-plan ``**SUPERSEDED`` banner near the top (title / leading
blockquote) is excluded: it was never executed (the maintainer pivoted to a
replacement), so its forward-looking Verifies aren't assertions about HEAD — e.g.
round-6's planned restore migration, dissolved by the metadata-single-source redesign.
The head-only anchor keeps a `**SUPERSEDED**` bold-mark on one sub-item of an
*executed* plan from silently dropping that whole plan's Verifies.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PLANS = sorted((_ROOT / "docs" / "done").glob("PLAN_round*_remediation.md"))  # docs/done ONLY

# A Verify FIELD spans a BARE **Verify:** → the effort marker **( (DOTALL, so a
# wrapped continuation-line command stays inside the field). The (?<!`) excludes
# backtick-wrapped prose mentions of `**Verify:**` (a plan about this parser has them).
_FIELD = re.compile(r"(?<!`)\*\*Verify:\*\*(.*?)(?:\*\*\(|\n- \*\*R|\Z)", re.DOTALL)
# A runnable command: `grep -c '<literal>' <path>` → <N>. Literal patterns only
# (the Python substring count below == grep -Fc, not grep -c's basic-regex).
_RUNNABLE = re.compile(r"`grep -c '([^']*)' (\S+?)`\s*(?:→|->)\s*`?(\d+)`?")
# A genuine command ATTEMPT: grep -c + a quote (vs a bare prose mention of the token).
_ATTEMPT = re.compile(r"`grep -c\s+['\"]")
_BRE_META = {".", "*", "[", "]", "^", "$", "\\"}
# A whole-plan **SUPERSEDED banner (bold all-caps, as round-6 carries) marks a plan
# that was NEVER executed — its forward-looking Verifies reference fixes abandoned
# for the replacement (round-6's planned 0072_restore_canyon_creek_sort_name
# migration was dissolved by the metadata-single-source redesign), so they are not
# HEAD assertions and are excluded. A lowercase prose "superseded" (round-3 has one)
# is NOT the banner and stays in scope.
_SUPERSEDED = re.compile(r"\*\*SUPERSEDED\b")


def _is_superseded(text: str) -> bool:
    """A whole-plan **SUPERSEDED banner lives in the title / leading blockquote, so
    only the file HEAD counts. A `**SUPERSEDED**` bold-mark on ONE sub-item deep in
    an *executed* plan's body must NOT silently drop that plan's whole Verify set."""
    head = "\n".join(text.splitlines()[:10])
    return bool(_SUPERSEDED.search(head))


def _fields() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in _PLANS:
        text = p.read_text("utf-8")
        if _is_superseded(text):  # never-executed plan — its Verifies aren't HEAD claims
            continue
        out.extend((p.name, f) for f in _FIELD.findall(text))
    return out


def _count(pattern: str, path: str) -> int:  # == grep -Fc: substring line-count
    return sum(1 for line in (_ROOT / path).read_text("utf-8").splitlines() if pattern in line)


def test_archived_grep_verifies_pass() -> None:
    """Every grep-checkable Verify in an archived plan still passes against HEAD."""
    ran = 0
    for plan, field in _fields():
        for pattern, path, n in _RUNNABLE.findall(field):
            assert not (set(pattern) & _BRE_META), (
                f"{plan}: non-literal grep -c pattern {pattern!r}"
            )
            got = _count(pattern, path)
            assert got == int(n), (
                f"{plan}: `grep -c {pattern!r} {path}` = {got}, but the plan's Verify says {n} "
                "— a fix recorded done no longer matches the source (or the source regressed)."
            )
            ran += 1
    assert ran, "no runnable grep -c Verify found in docs/done — the parser regressed?"


def test_no_unparsed_grep_c_attempt() -> None:
    """Counter (b): a grep -c command attempt the grammar can't parse must not slip
    through silently (else a future Verify drops out of coverage unnoticed)."""
    unparsed = []
    for plan, field in _fields():
        for m in _ATTEMPT.finditer(field):
            if not _RUNNABLE.match(field, m.start()):
                unparsed.append((plan, field[m.start() : m.start() + 60]))
    assert not unparsed, (
        f"grep -c command attempt(s) the Verify grammar didn't parse: {unparsed} "
        "— rewrite the Verify as `grep -c '<literal>' <path>` → `<N>`."
    )


def test_guard_is_non_vacuous() -> None:
    """The extractor must actually find the R1.1 DELETE-FROM-pages Verify, so a
    broken regex can't make the guard pass on an empty set."""
    patterns = [pat for _, f in _fields() for pat, *_ in _RUNNABLE.findall(f)]
    assert any("DELETE FROM pages" in p for p in patterns), (
        "extractor failed to find round-4 R1.1's `grep -c 'DELETE FROM pages' …` Verify"
    )


def test_superseded_exclusion_is_head_anchored() -> None:
    """The whole-plan banner excludes only at the top (title / leading blockquote);
    a `**SUPERSEDED**` mark deep in an *executed* plan's body must not — else one
    bolded sub-item would silently drop that plan's entire Verify set."""
    head = "# Plan\n\n> **SUPERSEDED by X.** pivoted.\n\n" + "body line\n" * 20
    body = "# Plan\n\n" + "real content\n" * 20 + "- this sub-item was **SUPERSEDED** later\n"
    assert _is_superseded(head)
    assert not _is_superseded(body)
