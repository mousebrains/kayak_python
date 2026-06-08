"""Writer-boundary guard — Python engine code must not mutate dataset-owned tables.

dataset-separation SA / acceptance criterion #6: outside ``sync-metadata``, schema
migrations, and test/scratch helpers, **Python** engine code under ``src/kayak/`` may
not issue DML/ORM mutations against a **dataset-owned** metadata table (the ones
projected to the dataset CSVs — ``layout.CONTRACT_CSVS``). Dataset-owned metadata
changes go through a reviewed CSV diff + ``levels sync-metadata``.

This is an AST scan that **supplements review** — it enumerates the deliberate
exceptions explicitly (rather than relying on one broad regex) and fails when a NEW,
unlisted file mutates a dataset-owned table. It detects:

* ``update(Model)`` / ``delete(Model)`` / ``insert(Model)`` / ``sqlite_insert(Model)``
  and ``query(Model).update/delete(...)`` and ``bulk_update_mappings(Model, …)`` /
  ``bulk_insert_mappings(Model, …)`` where ``Model`` is a dataset-owned ORM class
  (named as ``Model`` or ``models.Model``);
* ``Model(...)`` instantiation of a dataset-owned class (constructing a row to add);
* ``session.add/delete/merge(row)`` plus an ORM attribute write ``row.col = …`` where
  ``row`` was bound from ``Model(...)``, ``session.get(Model, …)``, or a
  ``select(Model)`` / ``query(Model)`` result — including a ``for row in …:`` loop var;
* a literal SQL ``INSERT INTO`` / ``UPDATE`` / ``DELETE FROM`` against a dataset-owned
  table inside an ``execute``/``executemany``/``executescript``/``text`` call
  (raw SQL, including the literal parts of an f-string).

Known blind spots (it *supplements* review, it is not a proof): a model imported
under an alias (``from …models import Reach as R``) is matched by neither name; a row
bound through a domain helper that hides the ``select``/``get`` (``r = get_reach(…);
r.col = …``) isn't tainted. No live instance of either exists in ``src/kayak/`` today.

Out of scope (by design):

* **The PHP editor/review layer** (``src/kayak/web/php/``) does write ``reach`` /
  ``reach_class`` via the change-request approval flow — that's the *intentional*
  reviewed-edit path (nightly-snapshotted to ``kayak_data`` until SA-teardown). It's
  not Python and not scanned here. (The editor feature is also disabled in prod.)
* Dynamic SQL whose table name is interpolated (e.g. ``metadata_csv``'s
  ``f"INSERT INTO {table} …"``) — no static table name to match.

Neither is listed below; the allowlist is exactly the Python files the scan flags.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from kayak.dataset import layout
from kayak.db.models import Base

_SRC = Path(__file__).resolve().parents[1] / "src" / "kayak"

# Files allowed to mutate dataset-owned tables, each with WHY. The guard fails if a
# file outside this set mutates one (a new unguarded writer) OR if a listed file no
# longer does (a stale entry) — keeping the list exhaustive AND minimal.
# Granularity is per-FILE: allowlisting a file for one sanctioned writer also blesses
# any later writer added to the SAME file, so keep these files single-purpose (or
# split the writer out) rather than growing a second mutation into an allowlisted one.
ALLOWLIST: dict[str, str] = {
    "cli/init_db.py": "fresh-DB seeding (state/source/fetch_url from sources.yaml)",
    "cli/validate_dataset.py": "applies reach geom/gradient to a TEMP validation DB",
    "huc/assign.py": "assign-huc maintenance tool — refuses the configured prod DB (SA-3)",
    "db/reaches.py": "set_reach_huc, called only by the assign-huc maintenance tool",
    "db/observations.py": "put_rating_table — dormant rating-authoring helper (test-only)",
    "db/gauges.py": "delete_gauge — guarded Gauge-deletion chokepoint, test-only (no "
    "prod caller); normal deletes route through sync-metadata --allow-deletes",
}

# INSERT [OR ...] INTO <t> / UPDATE [OR ...] <t> / DELETE FROM <t> — capture <t>.
# Mirrors tests/test_scripts/test_migrations_schema_only.py.
_DML = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)"
    r'\s+["\'`]?(\w+)',
    re.IGNORECASE,
)
# Core/Query/Bulk DML that names a dataset-owned model class as its first arg.
_MUTATING_FUNCS = {"update", "delete", "insert", "sqlite_insert", "pg_insert"}
_BULK_DML_FUNCS = {"bulk_update_mappings", "bulk_insert_mappings"}
_QUERY_WRITE_METHODS = {"update", "delete"}  # `session.query(Model).update/delete(...)`
_SQL_SINKS = {"execute", "executemany", "executescript", "text"}
# ORM session writes that take an *instance* (not a model class) — the idiomatic
# `session.delete(row)` / `session.add(row)` path. We only flag these when the
# argument is a name we've tainted as a dataset-owned instance (below), so adding a
# *runtime*-table row (FetchState, latest_*, editor) is not flagged.
_ORM_WRITE_METHODS = {"add", "delete", "merge", "add_all", "bulk_save_objects"}
# Calls that *yield* a dataset-owned instance/rows: session.get(Model, …),
# select(Model), session.query(Model) — used to taint the bound name/loop var.
_MODEL_FETCHERS = {"get", "select", "query"}


def _dataset_owned() -> tuple[set[str], set[str]]:
    """(model-class names, table names) for the dataset-owned CONTRACT_CSVS tables."""
    tables = set(layout.CONTRACT_CSVS)
    classes = {
        mapper.class_.__name__
        for mapper in Base.registry.mappers
        if mapper.local_table is not None and mapper.local_table.name in tables
    }
    return classes, tables


def _func_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _literal_sql(node: ast.expr) -> str:
    """Concatenate the string-literal parts of ``node`` (a str, f-string, or
    ``"a" + "b"`` concat); FormattedValue/interpolated parts contribute nothing."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal_sql(node.left) + " " + _literal_sql(node.right)
    return ""


def _is_model_ref(node: ast.expr | None, models: set[str]) -> bool:
    """A reference to a dataset-owned model class: ``Reach`` or ``models.Reach``."""
    return (isinstance(node, ast.Name) and node.id in models) or (
        isinstance(node, ast.Attribute) and node.attr in models
    )


def _produces_model(node: ast.expr, models: set[str]) -> bool:
    """Whether evaluating ``node`` yields a dataset-owned ORM instance/rows — a model
    constructor or a ``get``/``select``/``query`` over a dataset-owned model anywhere
    inside (so ``session.scalars(select(Reach)).first()`` taints its target too)."""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        if _is_model_ref(sub.func, models):  # Model(...) / models.Model(...)
            return True
        if (
            _func_name(sub.func) in _MODEL_FETCHERS
            and sub.args
            and _is_model_ref(sub.args[0], models)
        ):
            return True
    return False


def _tainted_names(tree: ast.AST, models: set[str]) -> set[str]:
    """Names bound to a dataset-owned ORM instance/row — from an assignment RHS or a
    for-loop iterable that produces one — so a later ``row.col = …`` /
    ``session.delete(row)`` is flagged. Module-global (not per-scope): over-broad is
    the safe direction (a false flag → allowlist/fix; never a missed write)."""
    tainted: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _produces_model(node.value, models):
            tainted.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and _produces_model(node.iter, models)
        ):
            tainted.add(node.target.id)
    return tainted


def _attr_write_reasons(node: ast.Assign, tainted: set[str]) -> list[str]:
    """ORM attribute write on a fetched dataset-owned row: ``reach.huc = …``."""
    return [
        f"attr write {t.value.id}.{t.attr}"
        for t in node.targets
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id in tainted
    ]


def _query_chain_model(recv: ast.expr, models: set[str]) -> str | None:
    """Walk back a ``.query(Model).filter(...).…`` method chain from ``recv`` to the
    underlying ``query(Model)``; return the dataset-owned model name, or None. Catches
    the common targeted form ``query(M).filter(...).update/delete(...)``, not just the
    bare ``query(M).update(...)``."""
    while isinstance(recv, ast.Call) and isinstance(recv.func, ast.Attribute):
        if recv.func.attr == "query" and recv.args and _is_model_ref(recv.args[0], models):
            return _func_name(recv.args[0])
        recv = recv.func.value
    return None


def _model_dml_reasons(node: ast.Call, models: set[str]) -> list[str]:
    """DML/instantiation naming a dataset-owned model class: ``update/delete/insert(M)``,
    ``bulk_*_mappings(M, …)``, ``M(...)``, ``query(M)[.filter…].update/delete(...)``."""
    reasons: list[str] = []
    fn = _func_name(node.func)
    if (
        fn in (_MUTATING_FUNCS | _BULK_DML_FUNCS)
        and node.args
        and _is_model_ref(node.args[0], models)
    ):
        reasons.append(f"{fn}({_func_name(node.args[0])})")
    if _is_model_ref(node.func, models):
        reasons.append(f"{_func_name(node.func)}(...)")
    if isinstance(node.func, ast.Attribute) and node.func.attr in _QUERY_WRITE_METHODS:
        model = _query_chain_model(node.func.value, models)
        if model is not None:
            reasons.append(f"query({model}).….{node.func.attr}()")
    return reasons


def _orm_sink_reasons(node: ast.Call, tables: set[str], tainted: set[str]) -> list[str]:
    """An ORM session write of a tainted instance (``session.delete(row)``) or raw SQL
    DML on a dataset-owned table inside an ``execute``/``text`` sink."""
    reasons: list[str] = []
    fn = _func_name(node.func)
    if fn in _ORM_WRITE_METHODS:
        elts: list[ast.expr] = []
        for arg in node.args:
            elts.extend(arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg])
        reasons += [
            f"session.{fn}({e.id})" for e in elts if isinstance(e, ast.Name) and e.id in tainted
        ]
    if fn in _SQL_SINKS:
        for arg in node.args:
            hits = {m.lower() for m in _DML.findall(_literal_sql(arg))} & tables
            if hits:
                reasons.append(f"raw SQL DML on {sorted(hits)}")
    return reasons


def _violations(src: str, models: set[str], tables: set[str]) -> list[str]:
    """Reasons ``src`` mutates a dataset-owned table (empty list = clean)."""
    reasons: list[str] = []
    tree = ast.parse(src)
    tainted = _tainted_names(tree, models)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            reasons += _attr_write_reasons(node, tainted)
        elif isinstance(node, ast.Call):
            reasons += _model_dml_reasons(node, models)
            reasons += _orm_sink_reasons(node, tables, tainted)
    return reasons


def _scan_tree() -> dict[str, list[str]]:
    """{relative path: violation reasons} for every .py under src/kayak/."""
    models, tables = _dataset_owned()
    flagged: dict[str, list[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        reasons = _violations(path.read_text(encoding="utf-8"), models, tables)
        if reasons:
            flagged[path.relative_to(_SRC).as_posix()] = reasons
    return flagged


def test_no_unlisted_dataset_table_writer() -> None:
    """Every dataset-owned-table writer under src/kayak/ is an enumerated exception."""
    flagged = _scan_tree()
    unlisted = {p: r for p, r in flagged.items() if p not in ALLOWLIST}
    assert not unlisted, (
        "New engine-runtime writer(s) of dataset-owned metadata tables — route the "
        "change through a reviewed CSV + `levels sync-metadata`, or (if it's a "
        "deliberate maintenance/scratch tool) add it to ALLOWLIST in "
        f"tests/test_writer_boundary.py with a rationale:\n{unlisted}"
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every ALLOWLIST entry still names a real file that still writes — so the list
    can't rot into blessing files that no longer touch dataset tables."""
    flagged = _scan_tree()
    stale = sorted(p for p in ALLOWLIST if p not in flagged)
    assert not stale, (
        f"ALLOWLIST entries that no longer mutate a dataset-owned table (remove them): {stale}"
    )


def test_detector_actually_fires() -> None:
    """Positive control: the detector flags the canonical mutation patterns, so a
    green guard means 'no writer found', not 'detector broken'."""
    models, tables = _dataset_owned()
    samples = [
        "import x\ndef f(s):\n    s.add(Reach(id=1))\n",  # instantiate + add
        "from sqlalchemy import update\ndef f(s):\n    s.execute(update(Source).values(name='x'))\n",
        "def f(c):\n    c.execute('DELETE FROM gauge WHERE id = 1')\n",  # raw SQL
        "def f(c, col):\n    c.execute(f'UPDATE reach SET {col} = ?', (1,))\n",  # f-string
        "def f(s, i):\n    g = s.get(Gauge, i)\n    s.delete(g)\n",  # ORM delete of a fetched row
        "def f(s, i):\n    r = s.get(Reach, i)\n    r.huc = 'x'\n",  # ORM attr write (get)
        "def f(s):\n    s.query(Reach).update({'huc': 'x'})\n",  # query().update()
        "def f(s):\n    s.query(Gauge).delete()\n",  # query().delete()
        "def f(s):\n    s.query(Reach).filter(Reach.id == 1).update({'huc': 'x'})\n",  # chained
        "def f(s):\n    s.query(Gauge).filter_by(id=1).delete()\n",  # chained delete
        "def f(s):\n    s.bulk_update_mappings(Reach, [{'id': 1, 'huc': 'x'}])\n",  # bulk mappings
        "from sqlalchemy import select\ndef f(s):\n    r = s.scalars(select(Reach)).first()\n    r.huc = 'x'\n",  # select-result attr write
        "def f(s):\n    for r in s.query(Reach).all():\n        r.huc = 'x'\n",  # loop-var attr write
        "import kayak.db.models as m\ndef f(s):\n    s.add(m.Reach())\n",  # qualified model ref
    ]
    for src in samples:
        assert _violations(src, models, tables), f"detector missed: {src!r}"
    # And it does NOT flag read paths.
    clean = "from sqlalchemy import select\ndef f(s):\n    return s.get(Reach, 1), s.execute(select(Source))\n"
    assert not _violations(clean, models, tables)
