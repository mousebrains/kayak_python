"""Guard: every ``@pytest.mark.slow`` test must live in
``tests/test_scripts/test_kayak_deploy.py``.

CI runs the slow deploy tests in a **path-scoped** pass
(``pytest tests/test_scripts/test_kayak_deploy.py -m slow``), because collecting
the whole suite imports other test modules whose import-time ``os.environ`` side
effects leak into the staged-engine subprocess the deploy tests build — a plain
``pytest -m slow`` fails the activation tests, the path-scoped form passes (see
``.github/workflows/ci.yml``). The cost of that isolation: a ``slow``-marked test
added in any *other* file would silently never run in CI. This guard fails loudly
if one drifts — move it into the deploy file, or broaden the CI slow step.
"""

from __future__ import annotations

from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_SELF = Path(__file__).resolve()
_SLOW_FILE = "test_scripts/test_kayak_deploy.py"
_MARKER = "@pytest.mark." + "slow"  # split so this guard file isn't its own match


def test_all_slow_marked_tests_live_in_the_deploy_file() -> None:
    offenders = [
        p.relative_to(_TESTS).as_posix()
        for p in _TESTS.rglob("test_*.py")
        if p.resolve() != _SELF
        and p.relative_to(_TESTS).as_posix() != _SLOW_FILE
        and _MARKER in p.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        f"slow-marked tests outside tests/{_SLOW_FILE} won't run in CI's "
        "path-scoped slow pass — move them there or broaden the CI slow step:\n  "
        + "\n  ".join(offenders)
    )
