"""GeoJSON generation for the reach map."""

import json
import math
from datetime import UTC
from typing import Any

from kayak.db.models import DataType, Gauge, LatestGaugeObservation, Reach
from kayak.utils.class_tiers import parse_class_tiers
from kayak.utils.pubhash import encode as pubhash_encode
from kayak.utils.simplify import parse_geom, simplify
from kayak.web.build._shared import _LICENSE_META
from kayak.web.build.gauges import (
    _gauge_observation_age,
    _gauge_status_from_reaches,
    _merge_gauge_observations,
)
from kayak.web.build.levels import _get_row_data

# GeoJSON geometry simplification. Coordinate precision is matched to the
# simplify epsilon - quantizing below the simplification grid would be wasted
# bytes. At 44N, 1e-5 deg ~= 0.8-1.1 m (below NHD's horizontal accuracy);
# 3e-4 deg ~= 24-33 m, which keeps polygonalization invisible up to ~zoom 14.
GEOJSON_SIMPLIFY_EPSILON = 0.0003
GEOJSON_COORD_PRECISION = 5
assert math.ceil(-math.log10(GEOJSON_SIMPLIFY_EPSILON)) + 1 <= GEOJSON_COORD_PRECISION


def _reach_geometry(reach: Reach, epsilon: float) -> dict | None:
    """Return a GeoJSON geometry dict for *reach* (simplified + rounded) or None.

    Falls back from WKT ``geom`` → start/end lat-lon pair → single lat-lon point.
    """
    p = GEOJSON_COORD_PRECISION
    if reach.geom:
        points = parse_geom(reach.geom)
        if len(points) >= 2:
            simplified = simplify(points, epsilon)
            return {
                "type": "LineString",
                "coordinates": [[round(x, p), round(y, p)] for x, y in simplified],
            }
        if len(points) == 1:
            pt = points[0]
            return {"type": "Point", "coordinates": [round(pt[0], p), round(pt[1], p)]}
    if (
        reach.latitude_start is not None
        and reach.longitude_start is not None
        and reach.latitude_end is not None
        and reach.longitude_end is not None
    ):
        return {
            "type": "LineString",
            "coordinates": [
                [round(float(reach.longitude_start), p), round(float(reach.latitude_start), p)],
                [round(float(reach.longitude_end), p), round(float(reach.latitude_end), p)],
            ],
        }
    if reach.latitude is not None and reach.longitude is not None:
        return {
            "type": "Point",
            "coordinates": [round(float(reach.longitude), p), round(float(reach.latitude), p)],
        }
    return None


def _build_reaches_static(
    reaches: list[Reach],
    epsilon: float = GEOJSON_SIMPLIFY_EPSILON,
) -> str:
    """Static per-reach geometry + metadata.

    Changes only when a reach is edited or retraced, so this file is
    long-cached by the browser (the hourly rebuild produces identical
    bytes most of the time).
    """
    features: list[dict] = []
    for reach in reaches:
        geometry = _reach_geometry(reach, epsilon)
        if geometry is None:
            continue
        tiers: set[str] = set()
        for c in reach.classes:
            tiers.update(parse_class_tiers(c.name))
        ordered_tiers = sorted(tiers, key=lambda t: ("I", "II", "III", "IV", "V").index(t))
        props = {
            "id": reach.id,
            "h": pubhash_encode(reach.id),
            "name": reach.display_name or reach.name or "",
            "tiers": ordered_tiers or ["?"],
            "state": reach.states[0].name if reach.states else "",
        }
        features.append({"type": "Feature", "properties": props, "geometry": geometry})
    return json.dumps(
        {"_meta": _LICENSE_META, "type": "FeatureCollection", "features": features},
        separators=(",", ":"),
    )


_POPUP_PRIMARY_ORDER: tuple[tuple[DataType, str, str], ...] = (
    (DataType.flow, "flow", "cfs"),
    (DataType.inflow, "flow", "cfs"),
    (DataType.gauge, "gage", "ft"),
)


def _build_reaches_state(
    reaches: list[Reach],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> str:
    """Per-reach popup data — the bit that changes every build.

    Per-reach entry keys (all but ``s`` optional):
      s   status: low|okay|high|unknown
      t   primary data-type label: "flow" or "gage"
      v   primary numeric value (int for flow, 2-dp float for gage)
      u   unit short form: "cfs" or "ft"
      d   delta_per_hour (omitted when null)
      ts  observed_at as UTC ISO ending in "Z"

    Temperature is intentionally skipped; popup priority is flow > inflow
    (rendered as flow) > gage. Status comes from ``_get_row_data`` so it
    matches the listing pages even when the level threshold lives on a
    different data type than the displayed value.
    """
    out: dict[str, dict] = {"_meta": _LICENSE_META}
    for reach in reaches:
        # Only emit reaches whose geometry also makes it into the static
        # file; otherwise the client would carry state it cannot paint.
        if _reach_geometry(reach, GEOJSON_SIMPLIFY_EPSILON) is None:
            continue
        row = _get_row_data(reach, calculated_gauge_ids, all_latest)
        entry: dict = {"s": row.get("status", "unknown")}

        gauge = reach.gauge
        if gauge is not None:
            for dtype, label, unit in _POPUP_PRIMARY_ORDER:
                cand = all_latest.get((gauge.id, dtype))
                if cand is None or cand.value is None:
                    continue
                entry["t"] = label
                if label == "flow":
                    entry["v"] = round(float(cand.value))
                else:
                    entry["v"] = round(float(cand.value), 2)
                entry["u"] = unit
                if cand.delta_per_hour is not None:
                    entry["d"] = round(float(cand.delta_per_hour), 2)
                if cand.observed_at is not None:
                    obs = cand.observed_at
                    obs = obs.replace(tzinfo=UTC) if obs.tzinfo is None else obs.astimezone(UTC)
                    entry["ts"] = obs.isoformat().replace("+00:00", "Z")
                break

        out[str(reach.id)] = entry
    return json.dumps(out, separators=(",", ":"))


# Per-data-type label + unit pair for the gauges-state.json schema. Keys
# in the emitted entry use the label; values are {v, u} objects with the
# unit string baked in so map.js doesn't need its own unit table.
_GAUGE_STATE_DTYPES: tuple[tuple[str, DataType, str], ...] = (
    ("flow", DataType.flow, "cfs"),
    ("inflow", DataType.inflow, "cfs"),
    ("gage", DataType.gauge, "ft"),
    ("temperature", DataType.temperature, "°F"),
)


def _build_gauges_static(gauges: list[Gauge]) -> str:
    """Static per-gauge geometry + metadata for the map's gauge layer.

    Skips gauges without a (lat, lon) — the map can't paint them.
    Long-cached alongside reaches-geom.json. Per Item 2a of
    ``docs/done/PLAN_map_and_ui_tweaks.md``.
    """
    p = GEOJSON_COORD_PRECISION
    features: list[dict] = []
    for g in gauges:
        if g.latitude is None or g.longitude is None:
            continue
        props: dict[str, Any] = {
            "name": g.display_name or g.river or g.location or "",
            "river": g.river or "",
            "location": g.location or "",
            "state": g.state or "",
        }
        if g.drainage_area is not None:
            props["drainage_area"] = float(g.drainage_area)
        if g.elevation is not None:
            props["elevation"] = float(g.elevation)
        features.append(
            {
                "type": "Feature",
                "id": g.id,
                "h": pubhash_encode(g.id),
                "properties": props,
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(float(g.longitude), p), round(float(g.latitude), p)],
                },
            }
        )
    return json.dumps(
        {"_meta": _LICENSE_META, "type": "FeatureCollection", "features": features},
        separators=(",", ":"),
    )


def _build_gauges_state(
    gauges: list[Gauge],
    calculated_gauge_ids: set[int],
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> str:
    """Per-gauge popup data for the map's gauge layer.

    Per-gauge entry keys (all but ``s`` optional):

      s            status: low|okay|high|unknown — rolled up from the
                   gauge's associated reaches by
                   :func:`_gauge_status_from_reaches` (matches the
                   gauges.html table's color).
      flow         {"v": int, "u": "cfs"} when flow OR inflow is current
      gage         {"v": float, "u": "ft"} when gage is current
      temperature  {"v": float, "u": "°F"} when temperature is current
      ts           observed_at as UTC ISO ending in "Z"
      stale        true when 1 day < age <= 7 days (rendered at 0.5
                   opacity on the map). Omitted when fresh.

    Drops gauges with an expired observation (>7 days) — same threshold
    gauges.html uses. Skips gauges without lat/long (the static file
    already omits them so the client wouldn't paint anyway). Per Item 2a
    of ``docs/done/PLAN_map_and_ui_tweaks.md``.
    """
    out: dict[str, object] = {"_meta": _LICENSE_META}
    for g in gauges:
        if g.latitude is None or g.longitude is None:
            continue
        # Pull every fresh observation onto a temp row so the staleness
        # rollup can pick the latest "time" across data types.
        row: dict[str, Any] = {}
        _merge_gauge_observations(row, g.id, all_latest)
        age = _gauge_observation_age(row)
        if age == "expired":
            continue
        # Rollup uses the gauge's associated reaches; gauges without
        # scored reaches fall through to "unknown" (grey marker).
        status, _counts = _gauge_status_from_reaches(
            list(g.reaches), calculated_gauge_ids, all_latest
        )
        entry: dict[str, Any] = {"s": status or "unknown"}
        entry.update(_gauge_state_readings(g.id, all_latest))
        obs_time = row.get("time")
        if obs_time is not None:
            obs_time = (
                obs_time.replace(tzinfo=UTC)
                if obs_time.tzinfo is None
                else obs_time.astimezone(UTC)
            )
            entry["ts"] = obs_time.isoformat().replace("+00:00", "Z")
        if age == "stale":
            entry["stale"] = True
        out[str(g.id)] = entry
    return json.dumps(out, separators=(",", ":"))


def _gauge_state_readings(
    gauge_id: int,
    all_latest: dict[tuple[int, DataType], LatestGaugeObservation],
) -> dict[str, dict[str, Any]]:
    """Pick up to one reading per popup key (flow/gage/temperature).

    Inflow collapses into the ``flow`` slot when no direct flow exists —
    same priority chain as :func:`_merge_gauge_observations`. Each
    value is rounded by data type: flow → int, temperature → 1 dp,
    gage → 2 dp.
    """
    out: dict[str, dict[str, Any]] = {}
    for label, dtype, unit in _GAUGE_STATE_DTYPES:
        latest = all_latest.get((gauge_id, dtype))
        if latest is None or latest.value is None:
            continue
        key = "flow" if label == "inflow" else label
        if key in out:
            continue
        raw = float(latest.value)
        if key == "flow":
            v: float | int = round(raw)
        elif key == "temperature":
            v = round(raw, 1)
        else:
            v = round(raw, 2)
        out[key] = {"v": v, "u": unit}
    return out
