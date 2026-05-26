"""Assign HUC12 codes to reaches by point-in-polygon lookup against WBD.

Reads the three WBD layers (HUC8/HU10/HUC12) from a single GeoPackage built
by ``scripts/extract_wbd.sh``. Builds an in-memory STRtree over HUC12
polygons, then for each reach with a put-in coordinate looks up the
containing polygon and writes its HUC12 code back to ``reach.huc``.

Also upserts every HUC8/10/12 ``(code, level, name, states)`` row into the
``huc_name`` lookup so the front-end can render readable filter labels.

Heavy spatial imports happen at module import time (geopandas, shapely);
the CLI shim ``kayak.cli.assign_huc`` defers loading this module until the
``levels assign-huc`` subcommand actually runs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

import geopandas as gpd
import pyogrio
from shapely.geometry import Point
from shapely.strtree import STRtree
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from kayak.db.engine import get_session
from kayak.db.models import HucName, Reach
from kayak.db.reaches import (
    get_reach,
    iter_reaches_with_putin,
    set_reach_huc,
)

logger = logging.getLogger(__name__)


def load_huc12(gpkg: Path) -> tuple[STRtree, list[str]]:
    """Load the WBDHU12 layer; return (STRtree of polygons, parallel list of HUC12 codes).

    The two outputs are index-aligned: ``codes[i]`` is the HUC12 of the
    polygon at tree-internal index ``i``. Reads only the HUC12 column to
    keep peak memory low on small VMs.
    """
    gdf = gpd.read_file(gpkg, layer="WBDHU12", columns=["HUC12"])
    if "HUC12" not in gdf.columns:
        raise ValueError(f"{gpkg}::WBDHU12 missing HUC12 column; got {list(gdf.columns)}")
    codes = [str(c) for c in gdf["HUC12"].tolist()]
    tree = STRtree(list(gdf.geometry))
    logger.info("Loaded %d HUC12 polygons from %s", len(codes), gpkg)
    return tree, codes


def assign_one(tree: STRtree, codes: list[str], lat: float, lon: float) -> str | None:
    """Find the HUC12 containing (lat, lon); ``None`` if outside coverage.

    Uses ``predicate="within"`` because shapely 2.x evaluates the predicate as
    ``input.predicate(tree_geom)`` — i.e. ``point.within(polygon)``. Using
    ``"contains"`` would ask if the point contains the polygon, which is never
    true for a point input.
    """
    pt = Point(lon, lat)
    idxs = tree.query(pt, predicate="within")
    if len(idxs) == 0:
        return None
    return codes[int(idxs[0])]


def upsert_huc_names(session: Session, gpkg: Path) -> int:
    """Read the WBD HUC6 + HUC8 attribute tables; bulk-upsert into ``huc_name``.

    Only these two levels are kept — they're the only ones any reader resolves
    (review-3 R6.2). Tolerates a missing layer (older ``wbd.gpkg`` files may
    lack one), skipping what isn't there.

    Returns the total number of rows written.
    """
    total = 0
    # Only HUC6 + HUC8 names are read anywhere (build/levels.py, build/gauges.py,
    # huc/assign.py's HUC8 lookup, gauge_picker.php, custom_gauges_handler.php —
    # all filter level IN (6, 8)). HUC2/4/10/12 were ~97% of the table and unread,
    # dropped 2026-05 (review-3 R6.2 / migration 0061). Reach HUC *codes* come from
    # WBD geometry (load_huc12), not this name table, so assignment is unaffected.
    for layer, level, code_col in (
        ("WBDHU6", 6, "HUC6"),
        ("WBDHU8", 8, "HUC8"),
    ):
        # Attribute-only read: skip geometry to keep peak memory low.
        try:
            df = pyogrio.read_dataframe(
                gpkg,
                layer=layer,
                columns=[code_col, "Name", "States"],
                read_geometry=False,
            )
        except Exception as exc:
            logger.info("Skipping %s — not in %s (%s)", layer, gpkg, exc)
            continue
        rows = []
        for row in df.itertuples(index=False):
            code = getattr(row, code_col, None)
            if code is None:
                continue
            name = getattr(row, "Name", None)
            states = getattr(row, "States", None)
            rows.append(
                {
                    "code": str(code),
                    "level": level,
                    "name": str(name) if name is not None else "",
                    "states": str(states) if states is not None else None,
                }
            )
        if not rows:
            continue
        # Chunk to keep parameter count under SQLite's limit (32766 since 3.32).
        for chunk in _chunks(rows, 500):
            stmt = sqlite_insert(HucName).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["code"],
                set_={
                    "level": stmt.excluded.level,
                    "name": stmt.excluded.name,
                    "states": stmt.excluded.states,
                },
            )
            session.execute(stmt)
        total += len(rows)
        logger.info("Upserted %d %s rows into huc_name", len(rows), layer)

    return total


def _chunks(seq: list[dict], n: int) -> Iterable[list[dict]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _assign_huc_to_reach(
    session: Session,
    reach: Reach,
    tree: STRtree,
    codes: list[str],
    huc8_name_map: dict[str, str],
    *,
    dry_run: bool,
    counts: dict[str, int],
) -> None:
    """Per-reach point-in-polygon lookup + conditional update; mutates `counts`."""
    if reach.latitude_start is None or reach.longitude_start is None:
        counts["no_coords"] += 1
        return
    huc = assign_one(tree, codes, float(reach.latitude_start), float(reach.longitude_start))
    if huc is None:
        counts["outside_coverage"] += 1
        logger.warning(
            "reach %d (%s) at (%.5f, %.5f) outside HUC12 coverage",
            reach.id,
            reach.name,
            reach.latitude_start,
            reach.longitude_start,
        )
        return

    new_basin = huc8_name_map.get(huc[:8])
    huc_changed = huc != reach.huc
    basin_changed = new_basin is not None and new_basin != reach.basin

    if not (huc_changed or basin_changed):
        counts["unchanged"] += 1
        return

    counts["assigned"] += 1
    if huc_changed:
        counts["huc_changed"] += 1
    if basin_changed:
        counts["basin_changed"] += 1

    if dry_run:
        logger.info(
            "[dry-run] reach %d huc %s -> %s, basin %r -> %r",
            reach.id,
            reach.huc,
            huc,
            reach.basin,
            new_basin,
        )
    else:
        set_reach_huc(
            session,
            reach.id,
            huc,
            basin=new_basin if basin_changed else None,
        )


def run(
    *,
    gpkg: str | Path = "Trace-cache/wbd.gpkg",
    reach_id: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Assign HUC12 to every reach (or one with ``--reach-id``).

    Returns a counts dict keyed ``assigned`` (huc and/or basin written),
    ``huc_changed``, ``basin_changed`` (diagnostic sub-counts), ``unchanged``,
    ``outside_coverage``, ``no_coords``.
    """
    gpkg_path = Path(gpkg)
    if not gpkg_path.exists():
        raise FileNotFoundError(
            f"{gpkg_path} not found — run scripts/extract_wbd.sh first "
            "(needs Trace-cache/NHD/hr/ HUC4 GDB ZIPs)"
        )

    counts: dict[str, int] = defaultdict(int)
    session = get_session()
    try:
        # Upsert names FIRST (attribute-only reads, small memory) so the per-layer
        # DataFrames are released before we hold the HUC12 STRtree+GeoSeries
        # for the rest of the run. Matters on small VMs.
        if not dry_run:
            upsert_huc_names(session, gpkg_path)
            session.commit()

        tree, codes = load_huc12(gpkg_path)

        # HUC8 -> name lookup so each reach's basin can mirror its HUC8.
        huc8_name_map: dict[str, str] = {
            row.code: row.name for row in session.scalars(select(HucName).where(HucName.level == 8))
        }

        if reach_id is not None:
            reach = get_reach(session, reach_id)
            reaches = [reach] if reach is not None else []
        else:
            reaches = list(iter_reaches_with_putin(session))

        for reach in reaches:
            if reach is None:
                continue
            _assign_huc_to_reach(
                session,
                reach,
                tree,
                codes,
                huc8_name_map,
                dry_run=dry_run,
                counts=counts,
            )

        if not dry_run:
            session.commit()
    finally:
        session.close()

    summary = dict(counts)
    print(
        f"HUC assignment: assigned={summary.get('assigned', 0)} "
        f"(huc_changed={summary.get('huc_changed', 0)}, "
        f"basin_changed={summary.get('basin_changed', 0)}) "
        f"unchanged={summary.get('unchanged', 0)} "
        f"outside_coverage={summary.get('outside_coverage', 0)} "
        f"no_coords={summary.get('no_coords', 0)}" + (" (dry-run)" if dry_run else "")
    )
    return summary
