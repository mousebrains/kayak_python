"""Smoke-test the split-geojson build functions: shape, keys, tier encoding."""

from __future__ import annotations

import json

from kayak.db.models import Reach, ReachClass, State
from kayak.web.build.geojson import _build_reaches_state, _build_reaches_static


def _mk_reach(
    session,
    reach_id: int,
    *,
    name: str,
    display: str | None = None,
    geom: str | None = None,
    classes: list[str] | None = None,
    state: str | None = None,
) -> Reach:
    r = Reach(
        id=reach_id,
        name=name,
        display_name=display,
        sort_name=(display or name),
        geom=geom,
    )
    session.add(r)
    session.flush()
    for c in classes or []:
        session.add(ReachClass(reach_id=r.id, name=c))
    if state:
        st = session.query(State).filter_by(name=state).one_or_none() or State(name=state)
        if st.id is None:
            session.add(st)
            session.flush()
        r.states.append(st)
    session.flush()
    session.refresh(r)
    return r


def test_static_file_shape_and_tiers(session) -> None:
    a = _mk_reach(
        session,
        1,
        name="alpha",
        display="Alpha",
        geom="-122.1 44.1,-122.2 44.2,-122.3 44.3",
        classes=["III-IV(V)"],
        state="Oregon",
    )
    b = _mk_reach(
        session,
        2,
        name="beta",
        display="Beta",
        geom="-120.0 45.0,-120.1 45.1",
        classes=[],
    )
    _ = a, b  # silence unused

    raw = _build_reaches_static([a, b])
    doc = json.loads(raw)

    assert doc["type"] == "FeatureCollection"
    assert len(doc["features"]) == 2
    feats = {f["properties"]["id"]: f for f in doc["features"]}

    fa = feats[1]
    assert fa["properties"]["name"] == "Alpha"
    assert fa["properties"]["tiers"] == ["III", "IV"]  # crux V dropped
    assert fa["properties"]["state"] == "Oregon"
    assert fa["geometry"]["type"] == "LineString"
    # Precision rounding: 4 decimal places.
    for x, y in fa["geometry"]["coordinates"]:
        assert x == round(x, 4)
        assert y == round(y, 4)

    fb = feats[2]
    assert fb["properties"]["tiers"] == ["?"]
    assert fb["properties"]["state"] == ""


def test_state_file_emits_status_only_when_no_gauge(session) -> None:
    a = _mk_reach(
        session,
        10,
        name="r10",
        display="R10",
        geom="-122 44,-122.1 44.1",
    )
    b = _mk_reach(
        session,
        11,
        name="r11",
        display="R11",
        geom="-123 45,-123.1 45.1",
    )

    raw = _build_reaches_state([a, b], set(), {})
    doc = json.loads(raw)
    doc.pop("_meta", None)

    # Reaches with no gauge get a bare status entry — no v/u/d/ts.
    assert doc == {"10": {"s": "unknown"}, "11": {"s": "unknown"}}


def test_reach_without_geometry_is_skipped(session) -> None:
    r = _mk_reach(session, 99, name="no_geom", display="NoGeom", geom=None)
    static = json.loads(_build_reaches_static([r]))
    state = json.loads(_build_reaches_state([r], set(), {}))
    state.pop("_meta", None)
    assert static["features"] == []
    assert state == {}


def test_outputs_carry_license_meta(session) -> None:
    """Every generated JSON file embeds CC BY-NC 4.0 attribution at top level."""
    r = _mk_reach(session, 200, name="r200", display="R200", geom="-122 44,-122.1 44.1")
    static = json.loads(_build_reaches_static([r]))
    state = json.loads(_build_reaches_state([r], set(), {}))
    for doc in (static, state):
        assert doc["_meta"]["license"] == "CC BY-NC 4.0"
        assert doc["_meta"]["attribution"] == "levels.wkcc.org"
        assert doc["_meta"]["license_url"].startswith("https://creativecommons.org/")
