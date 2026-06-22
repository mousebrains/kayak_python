"""Patch a kayak_data dataset directory from an endorsed change_request diff.

Tier 3 of ``docs/PLAN_editor_pr_bridge.md``: pure, deterministic CSV editing by
stable ``id``. Given an approved ``change_request``'s ``applied_json`` fragment,
overwrite exactly the allowlisted cells in ``reach.csv`` / ``gauge.csv`` and
return what changed — no git, no network, no DB.

Design points (verified against the editor + dataset code):

* **applied_json shape.** Diff-only, new-values-only, keyed by table:
  ``{"reach": {col: new}, "reach_class": {...}}`` or ``{"gauge": {col: new}}``.
  ``change_request.target_id`` equals the CSV row ``id`` (sync-metadata matches by
  the same stable id), so this is a direct id → row lookup.
* **Allowlist.** Keys are restricted to the fields the PHP editor flows can
  change: for reach, the union of the propose freeze and edit.php's editable set;
  for gauge, edit.php's editable set. reach keys are server-allowlist-enforced at
  freeze (this re-checks as defense in depth); gauge is NOT filtered server-side,
  so the adapter's own allowlist is the only bound (keeps a tampered payload from
  freezing e.g. rating_id / state into a PR).
* **reach_class is intentionally unsupported** — its payload is a name/range set
  with no per-row ids, so it can't be applied to the id-bearing ``reach_class.csv``
  safely until the propose/review payload is made row-id-aware (see the plan).
* **Minimal diff.** Only the target row's line is rewritten, in the dataset's own
  csv dialect (LF + ``QUOTE_MINIMAL``, matching ``generate-sources`` /
  ``recover-metadata``), so a bridge PR shows exactly the cells that changed.
* **Drift / conflict.** If a changed cell's current value differs from the
  reviewed base captured at queue time, raise :class:`ConflictError` *before*
  writing — the worker turns that into the ``conflict`` state for re-review.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

# The reach fields an editor flow can change — all real reach.csv columns. The
# bridge accepts BOTH producers, so the allowlist is their union:
#   * propose.php (includes/reach_propose_fields.php) — text + coords, server
#     allowlist-enforced at freeze;
#   * edit.php (maintainer direct edit) — a broader set, NOT allowlist-filtered
#     server-side, so re-enforced here as defense in depth.
REACH_PROPOSE_FIELDS = frozenset(
    {
        "description",
        "features",
        "display_name",
        "latitude_start",
        "longitude_start",
        "latitude_end",
        "longitude_end",
    }
)
REACH_EDIT_FIELDS = frozenset(
    {
        "display_name",
        "sort_name",
        "description",
        "difficulties",
        "basin",
        "region",
        "length",
        "gradient",
        "elevation_lost",
        "season",
        "scenery",
        "features",
        "remoteness",
        "nature",
        "watershed_type",
        "optimal_flow",
        "notes",
    }
)
REACH_ALLOWED_FIELDS = REACH_PROPOSE_FIELDS | REACH_EDIT_FIELDS

# The gauge fields a maintainer can direct-edit (edit.php editable_fields) — all
# real gauge.csv columns. The gauge freeze path is NOT allowlist-filtered
# server-side, so this is the adapter's own defense-in-depth bound (keeps a
# tampered payload from freezing e.g. rating_id / state / display_name into a PR).
GAUGE_ALLOWED_FIELDS = frozenset(
    {
        "name",
        "location",
        "latitude",
        "longitude",
        "elevation",
        "drainage_area",
        "bank_full",
        "flood_stage",
        "huc",
        "station_id",
        "usgs_id",
        "cbtt_id",
        "geos_id",
        "nws_id",
        "nwsli_id",
        "snotel_id",
    }
)

# reach rows carry an updated_at stamp; bump it only when a substantive field
# actually changes (a no-op edit must not churn the row).
_REACH_STAMP_COLUMN = "updated_at"


class DatasetPatchError(ValueError):
    """A change can't be safely turned into a dataset edit — reject it loudly."""


class ConflictError(DatasetPatchError):
    """Dataset main drifted from the reviewed base since review — needs re-review."""


@dataclass
class PatchResult:
    """What one adapter changed in one CSV."""

    file: str  # relative filename, e.g. "reach.csv"
    target_id: int
    changed: dict[str, tuple[str, str]]  # field -> (before, after); empty == no-op

    @property
    def is_noop(self) -> bool:
        return not self.changed


def apply_change(
    dataset_dir: str | Path,
    target_type: str,
    target_id: int,
    applied_json: dict,
    *,
    updated_at: str,
    expected_base: dict[str, str] | None = None,
) -> list[PatchResult]:
    """Apply an endorsed diff to *dataset_dir*; return the per-file results.

    ``target_type`` is the ``change_request.target_type`` value (``reach`` /
    ``gauge``; ``site`` and ``source`` are unsupported). ``updated_at`` is the ISO
    timestamp to stamp on a changed reach row (the caller supplies it for
    determinism). ``expected_base`` maps each editor-changed field to its
    reviewed-base value for drift detection. Raises :class:`DatasetPatchError`
    (or :class:`ConflictError`) on anything it can't safely apply.
    """
    dataset_dir = Path(dataset_dir)
    tt = str(target_type)
    if tt == "reach":
        if "reach_class" in applied_json:
            raise DatasetPatchError(
                "reach_class bridging is not supported yet — its payload is not "
                "row-id-aware (see docs/PLAN_editor_pr_bridge.md)"
            )
        if "reach" not in applied_json:
            raise DatasetPatchError("reach change has no 'reach' diff to apply")
        return [
            _apply_reach(dataset_dir, target_id, applied_json["reach"], updated_at, expected_base)
        ]
    if tt == "gauge":
        if "gauge" not in applied_json:
            raise DatasetPatchError("gauge change has no 'gauge' diff to apply")
        return [_apply_gauge(dataset_dir, target_id, applied_json["gauge"], expected_base)]
    raise DatasetPatchError(f"unsupported target_type for bridging: {tt!r}")


def _apply_reach(
    dataset_dir: Path,
    target_id: int,
    diff: object,
    updated_at: str,
    expected_base: dict[str, str] | None,
) -> PatchResult:
    updates = _validate_diff(diff, allowed=REACH_ALLOWED_FIELDS, label="reach")
    return _patch_row(
        dataset_dir / "reach.csv",
        target_id,
        updates,
        expected_base=expected_base,
        stamp={_REACH_STAMP_COLUMN: updated_at},
    )


def _apply_gauge(
    dataset_dir: Path,
    target_id: int,
    diff: object,
    expected_base: dict[str, str] | None,
) -> PatchResult:
    path = dataset_dir / "gauge.csv"
    updates = _validate_diff(diff, allowed=GAUGE_ALLOWED_FIELDS, label="gauge")
    return _patch_row(path, target_id, updates, expected_base=expected_base, stamp=None)


def _validate_diff(
    diff: object, *, allowed: frozenset[str] | set[str], label: str
) -> dict[str, str]:
    """Type-check the diff and enforce the field allowlist; coerce values to cells."""
    if not isinstance(diff, dict) or not diff:
        raise DatasetPatchError(f"{label} diff must be a non-empty object")
    bad = sorted(set(diff) - set(allowed))
    if bad:
        raise DatasetPatchError(f"{label} field(s) not allowed: {bad}")
    return {str(field): _cell(field, value, label) for field, value in diff.items()}


def _cell(field: str, value: object, label: str) -> str:
    """A diff value → its CSV cell.

    The dataset CSVs store everything as text, but the PHP producers cast numeric
    fields (coordinates, length, gradient, elevation, …) to float before
    ``json_encode``, so ``applied_json`` carries JSON numbers for them. Render
    str/None/int/float to a cell; reject anything else (a list/dict/bool would
    mean a malformed payload). ``float`` uses Python's shortest-round-trip repr,
    which matches PHP's ``serialize_precision=-1`` for the decimal coordinates in
    use — so an unchanged value won't spuriously diff.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        # bool is an int subclass; never a valid metadata cell here.
        raise DatasetPatchError(f"{label}.{field}: boolean values are not accepted")
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    raise DatasetPatchError(f"{label}.{field}: unsupported value type {type(value).__name__}")


def _serialize_row(header: list[str], row: dict[str, str]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerow([row[c] for c in header])
    return buf.getvalue()


def _patch_row(
    csv_path: Path,
    target_id: int,
    updates: dict[str, str],
    *,
    expected_base: dict[str, str] | None,
    stamp: dict[str, str] | None,
) -> PatchResult:
    """Overwrite ``updates`` (+ optional ``stamp``) on the row with ``id==target_id``.

    Rewrites only that row's line when the file has one physical line per row (the
    invariant for the dataset CSVs); falls back to a full re-serialize otherwise
    (still correct, just a larger diff). Drift is checked on the ``updates`` fields
    (not ``stamp``) against ``expected_base`` before any write.
    """
    text = csv_path.read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    cols_used = set(updates) | (set(stamp) if stamp else set())
    header = _validated_header(csv_path, rows, cols_used)
    target_j, row = _locate_row(csv_path, rows, header, target_id)
    _ensure_no_drift(csv_path, target_id, row, updates, expected_base)

    changed = _apply_updates(row, updates)
    if not changed:
        return PatchResult(csv_path.name, target_id, {})  # true no-op: don't stamp/write
    if stamp:
        changed.update(_apply_updates(row, stamp))

    _rewrite_row(csv_path, text, rows, target_j, header, row)
    return PatchResult(csv_path.name, target_id, changed)


def _validated_header(csv_path: Path, rows: list[list[str]], cols_used: set[str]) -> list[str]:
    if not rows:
        raise DatasetPatchError(f"{csv_path.name}: empty file")
    header = rows[0]
    if "id" not in header:
        raise DatasetPatchError(f"{csv_path.name}: no 'id' column")
    unknown = [c for c in sorted(cols_used) if c not in header]
    if unknown:
        raise DatasetPatchError(f"{csv_path.name}: unknown column(s) {unknown}")
    return header


def _locate_row(
    csv_path: Path, rows: list[list[str]], header: list[str], target_id: int
) -> tuple[int, dict[str, str]]:
    id_idx = header.index("id")
    for j in range(1, len(rows)):
        if rows[j] and rows[j][id_idx] == str(target_id):
            if len(rows[j]) != len(header):
                # A ragged row (too few/many cells) would silently drop or
                # mis-map fields on rewrite — refuse loudly instead.
                raise DatasetPatchError(
                    f"{csv_path.name}: row id {target_id} has {len(rows[j])} cells, "
                    f"expected {len(header)}"
                )
            return j, dict(zip(header, rows[j], strict=True))
    raise DatasetPatchError(f"{csv_path.name}: id {target_id} not found")


def _ensure_no_drift(
    csv_path: Path,
    target_id: int,
    row: dict[str, str],
    updates: dict[str, str],
    expected_base: dict[str, str] | None,
) -> None:
    if expected_base is None:
        return
    drifted = {
        f: (expected_base[f], row.get(f, ""))
        for f in updates
        if f in expected_base and row.get(f, "") != expected_base[f]
    }
    if drifted:
        raise ConflictError(
            f"{csv_path.name} id {target_id} drifted from the reviewed base: {drifted}"
        )


def _apply_updates(row: dict[str, str], updates: dict[str, str]) -> dict[str, tuple[str, str]]:
    """Apply updates to ``row`` in place; return only the fields that changed."""
    changed: dict[str, tuple[str, str]] = {}
    for field, new in updates.items():
        before = row.get(field, "")
        if before != new:
            changed[field] = (before, new)
            row[field] = new
    return changed


def _rewrite_row(
    csv_path: Path,
    text: str,
    rows: list[list[str]],
    target_j: int,
    header: list[str],
    row: dict[str, str],
) -> None:
    # read_text() normalised newlines to LF; the dataset CSVs are LF (csv.writer
    # via generate-sources/recover-metadata), so we re-emit LF and don't churn
    # the whole file. (A CRLF dataset would be normalised to LF on first edit.)
    new_line = _serialize_row(header, row)
    lines = text.splitlines(keepends=True)
    if len(lines) == len(rows):  # one physical line per row → minimal diff
        # Preserve the target line's trailing-newline state (the last row of a
        # file without a final newline must not gain one).
        if not lines[target_j].endswith("\n"):
            new_line = new_line.rstrip("\n")
        lines[target_j] = new_line
        csv_path.write_text("".join(lines), encoding="utf-8")
    else:  # multi-line rows present → correct full re-serialize (rare)
        rows[target_j] = [row[c] for c in header]
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        csv_path.write_text(buf.getvalue(), encoding="utf-8")
