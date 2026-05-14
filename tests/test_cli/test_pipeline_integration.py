"""End-to-end integration test for the pipeline CLI.

Unlike ``test_pipeline.py`` (which inspects step order and exercises
fail-fast branches with mocks), this test seeds a real SQLite DB, feeds
the fetch step a canned response via ``input_dir``, then runs
fetch → calc-rating → calculator → build and asserts the generated
``index.html`` contains the seeded reach.

Parametrized across all six registered parsers (nwps, nwrfc.xml,
nwrfc.textplot, usace.cda, usbr, wa.gov) — each entry plumbs a fake
URL → file mapping through ``input_dir``, mints a one-row source +
gauge + reach, and verifies the reach lands in the rendered HTML.

Catches regressions that individual-stage tests miss: schema / model /
parser drift, cache-update ordering, build HTML changes, and per-parser
URL→source mapping. Six fixtures collectively run in ~2s; not marked
slow, so default pytest exercises the path.

The build step shells out to ``setfacl`` which isn't available on macOS;
``deploy.apply_acls`` silently no-ops in that case, so the test still
works on macOS dev machines (just without ACL coverage).
"""

from __future__ import annotations

import json
from argparse import Namespace
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from kayak.cli.pipeline import pipeline
from kayak.db import engine as engine_mod
from kayak.db.models import (
    Base,
    FetchUrl,
    Gauge,
    GaugeSource,
    Reach,
    ReachClass,
    ReachState,
    Source,
    State,
)


@dataclass(frozen=True)
class _ParserFixture:
    """One parser's slice of fixture data.

    ``url`` must encode whatever the parser uses to identify its source
    (e.g. NWPS reads the LID from ``/gauges/{LID}/``, nwrfc.textplot from
    ``?id=``). ``source_name`` must match what the parser will look up in
    ``source_map`` — for parsers that read the station name from the
    payload body (nwrfc.xml, usbr, wa.gov), it's whatever appears there.

    ``timezone`` is set to ``"UTC"`` for parsers that pass
    ``assume_naive=True`` (USBR, wa.gov); the test emits naive UTC
    timestamps and the ``"UTC"`` source row makes the localization a
    no-op. Parsers that produce tz-aware datetimes leave this ``None``.
    """

    parser_id: str
    url: str  # may contain {t1} / {t2} placeholders for recent timestamps
    sample_template: str  # ditto
    source_name: str
    reach_name: str
    expected_value: str
    timezone: str | None = None


def _recent_timestamps() -> tuple[datetime, datetime]:
    """Two timestamps 2h/1h ago, rounded to the minute."""
    now = datetime.now(UTC).replace(microsecond=0, second=0)
    return now - timedelta(hours=2), now - timedelta(hours=1)


# Per-parser fixture inventory. The flow values land inside the reach's
# 200..800 cfs ReachClass window (set in `_seed`) so the build classifies
# the observation as "okay" rather than dropping it as out-of-range.
_PARSER_FIXTURES: list[_ParserFixture] = [
    _ParserFixture(
        parser_id="nwps",
        url="test://nwps/gauges/NWPSLID/stageflow/observed",
        sample_template=json.dumps(
            {
                "primaryUnits": "ft",
                "secondaryUnits": "cfs",
                "data": [
                    {"validTime": "{t1}", "primary": 4.20, "secondary": 500.0},
                    {"validTime": "{t2}", "primary": 4.30, "secondary": 525.0},
                ],
            }
        ),
        source_name="NWPSLID",
        reach_name="NWPS Creek",
        expected_value="525",
    ),
    _ParserFixture(
        parser_id="nwrfc.xml",
        url="test://nwrfc-xml/observed.xml",
        sample_template=(
            '<?xml version="1.0"?>\n'
            "<forecast>\n"
            '  <SiteData id="NXMLID">\n'
            "    <observedData>\n"
            "      <dataDateTime>{t1}</dataDateTime>\n"
            '      <stage units="feet">4.20</stage>\n'
            '      <discharge units="cfs">510</discharge>\n'
            "    </observedData>\n"
            "    <observedData>\n"
            "      <dataDateTime>{t2}</dataDateTime>\n"
            '      <stage units="feet">4.30</stage>\n'
            '      <discharge units="cfs">535</discharge>\n'
            "    </observedData>\n"
            "  </SiteData>\n"
            "</forecast>\n"
        ),
        source_name="NXMLID",
        reach_name="NWRFC XML Creek",
        expected_value="535",
    ),
    _ParserFixture(
        parser_id="nwrfc.textplot",
        url="test://nwrfc-textplot/cgi?id=NTPLID&pe=HG",
        # No "(PDT)"/"(PST)" in the body, so parse_datetime stamps UTC on
        # naive timestamps (matching what we want for the test fixture).
        sample_template=(
            "<html><body><table>\n"
            "<tr><td>Date/Time</td><td>Stage</td><td>Discharge</td></tr>\n"
            "<tr><td>{t1}</td><td>4.20</td><td>520</td></tr>\n"
            "<tr><td>{t2}</td><td>4.30</td><td>545</td></tr>\n"
            "</table></body></html>\n"
        ),
        source_name="NTPLID",
        reach_name="NWRFC TextPlot Creek",
        expected_value="545",
    ),
    _ParserFixture(
        parser_id="usace.cda",
        # Parser hard-rejects URLs without 'timezone=GMT'.
        url="test://usace/getjson?query=USACE1&timezone=GMT&backward=2d&forward=0d",
        sample_template=json.dumps(
            {
                "USACE1": {
                    "name": "Test Dam",
                    "timeseries": {
                        "USACE1.Flow-Out.Inst.0.0.Best": {
                            "parameter": "Flow-Out",
                            "units": "cfs",
                            "values": [
                                ["{t1}", 515.0, 0],
                                ["{t2}", 555.0, 0],
                            ],
                        }
                    },
                }
            }
        ),
        source_name="USACE1",
        reach_name="USACE Creek",
        expected_value="555",
    ),
    _ParserFixture(
        parser_id="usbr",
        url="test://usbr/hydromet?format=csv",
        # USBR uses MM/DD/YYYY HH:MM and assume_naive=True; localization
        # via source.timezone="UTC" leaves the value unchanged.
        sample_template=("DateTime,USBR1_q,USBR1_gh\n{t1},505.0,4.20\n{t2},565.0,4.30\n"),
        source_name="USBR1",
        reach_name="USBR Creek",
        expected_value="565",
        timezone="UTC",
    ),
    _ParserFixture(
        parser_id="wa.gov",
        url="test://wa-gov/station/data.txt",
        # wa.gov is fixed-width: "STATION--description", DATE TIME header,
        # then dashed separator, then rows. Stage data type. Quality=100
        # is in-range (0 < q < 200). Source.timezone="UTC" + naive UTC
        # timestamps → stored as UTC.
        sample_template=(
            "WAGOV1--Test wa.gov Station\n"
            "DATE TIME Stage  Quality\n"
            "---  ---  -------  -------\n"
            "{t1}  4.20  100\n"
            "{t2}  4.30  100\n"
        ),
        source_name="WAGOV1",
        reach_name="wa.gov Creek",
        # wa.gov emits gauge-type data, so 4.30 (stage in feet) is what
        # lands in the cell rather than a flow number.
        expected_value="4.3",
        timezone="UTC",
    ),
]


def _format_timestamps(fixture: _ParserFixture) -> tuple[str, str]:
    """Return (url, sample) with the parser's preferred time format substituted.

    Each parser eats a different timestamp shape:
      * nwps                 — ``2025-…T…Z``
      * nwrfc.xml            — ``2025-…T…:…:…`` (parse_datetime, UTC stamp)
      * nwrfc.textplot       — ``YYYY-MM-DD HH:MM``
      * usace.cda            — ``2025-…T…:…:…``
      * usbr                 — ``MM/DD/YYYY HH:MM``
      * wa.gov               — ``MM/DD/YYYY HH:MM``
    """
    t1, t2 = _recent_timestamps()
    if fixture.parser_id == "nwps":
        f1, f2 = t1.strftime("%Y-%m-%dT%H:%M:%SZ"), t2.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif fixture.parser_id in ("nwrfc.xml", "usace.cda"):
        f1, f2 = t1.strftime("%Y-%m-%dT%H:%M:%S"), t2.strftime("%Y-%m-%dT%H:%M:%S")
    elif fixture.parser_id == "nwrfc.textplot":
        f1, f2 = t1.strftime("%Y-%m-%d %H:%M"), t2.strftime("%Y-%m-%d %H:%M")
    elif fixture.parser_id in ("usbr", "wa.gov"):
        f1, f2 = t1.strftime("%m/%d/%Y %H:%M"), t2.strftime("%m/%d/%Y %H:%M")
    else:
        raise ValueError(f"unknown parser_id {fixture.parser_id!r}")
    # str.replace, not str.format — the JSON fixtures contain literal
    # ``{`` / ``}`` that confuse format-spec parsing.
    sample = fixture.sample_template.replace("{t1}", f1).replace("{t2}", f2)
    return fixture.url, sample


def _seed(db_url: str, fixture: _ParserFixture) -> None:
    """Create schema + minimal fixtures against the on-disk test DB.

    Each fixture gets exactly one Source row linked to one FetchUrl,
    one Gauge, one Reach (Oregon-tagged), and a ReachClass that brackets
    the parser's emitted value as "okay". Source.timezone is set for
    parsers that emit naive datetimes; tz-aware parsers leave it NULL.
    """
    eng = create_engine(db_url)

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)

    with Session(eng) as s:
        s.add(State(name="Oregon", abbreviation="OR"))
        s.flush()

        fetch_url = FetchUrl(url=fixture.url, parser=fixture.parser_id, is_active=True)
        s.add(fetch_url)
        s.flush()

        source = Source(
            name=fixture.source_name,
            agency="TEST",
            fetch_url_id=fetch_url.id,
            timezone=fixture.timezone,
        )
        s.add(source)
        s.flush()

        gauge = Gauge(name=f"{fixture.source_name}_gauge", nws_id=fixture.source_name)
        s.add(gauge)
        s.flush()
        s.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))

        reach = Reach(
            name=fixture.reach_name,
            display_name=fixture.reach_name,
            sort_name=fixture.reach_name,
            gauge_id=gauge.id,
        )
        s.add(reach)
        s.flush()

        or_id = s.execute(select(State.id).where(State.abbreviation == "OR")).scalar_one()
        s.add(ReachState(reach_id=reach.id, state_id=or_id))

        # Window covers both flow (~525 cfs) and gauge (~4.30 ft) so a
        # single ReachClass works across all six parsers. The build's
        # classifier picks the data_type matching the threshold's
        # low_data_type / high_data_type; "flow" matches every parser
        # except wa.gov (which emits gauge), but wa.gov's stage of 4.30
        # still surfaces in the HTML even when the class can't bind.
        s.add(
            ReachClass(
                reach_id=reach.id,
                name="III",
                low=200.0,
                low_data_type="flow",
                high=800.0,
                high_data_type="flow",
            )
        )
        s.commit()


def _build_args(input_dir: Path, output_dir: Path) -> Namespace:
    """Build the full Namespace pipeline() expects, including fetch.addArgs_options keys."""
    return Namespace(
        skip_fetch=False,
        dry_run=False,
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        continue_on_error=False,
        # fetch optional args (full set from addArgs_options)
        fetch_only=False,
        ignore_constraints=True,
        concurrency=1,
        show_name=False,
        url_prefix="",
        parser_filter=None,
        parser_type=None,
        url_filter=None,
        single_url=None,
    )


def _write_input_file(input_dir: Path, raw_url: str, body: str) -> None:
    """Plant the fake response where fetch's input_dir resolution will find it.

    fetch's path resolver does ``input_dir / raw_url.lstrip('/')``, so a
    URL like ``test://nwps/gauges/X/...`` lands at
    ``input_dir/test:/nwps/gauges/X/...``.
    """
    target = input_dir / raw_url.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)


@pytest.mark.parametrize("fixture", _PARSER_FIXTURES, ids=lambda f: f.parser_id)
def test_pipeline_fetch_through_build(fixture: _ParserFixture, tmp_path: Path) -> None:
    """fetch (via canned ``input_dir`` file) → … → build, per parser.

    Asserts the seeded reach name lands in ``index.html`` and at least
    one emitted observation value appears too. That's the "wires are
    connected end-to-end" smoke check — the parser-specific unit tests
    in ``tests/test_parsers/`` already vet edge cases.
    """
    db_path = tmp_path / "kayak.db"
    db_url = f"sqlite:///{db_path}"
    _seed(db_url, fixture)

    url, sample_body = _format_timestamps(fixture)
    input_dir = tmp_path / "input"
    _write_input_file(input_dir, url, sample_body)

    output_dir = tmp_path / "out"
    args = _build_args(input_dir, output_dir)

    # Single-entry source list matching our seeded FetchUrl so fetch()
    # doesn't iterate the 100+ sources from real data/sources.yaml.
    yaml_sources = [{"url": url, "parser": fixture.parser_id, "hours": ""}]

    # OUTPUT_DIR override routes build()'s path lookup onto our tmp dir.
    # Stub fetch-usgs-ogc to avoid hitting the real USGS OGC endpoint.
    # Stub sync_sources so it doesn't re-seed sources.yaml's full row
    # set (the orphan-check step would then flag every unlinked source).
    engine_mod.reset()
    engine_mod.get_engine(db_url)
    engine_mod.get_session_factory(db_url)

    with (
        patch.dict("os.environ", {"DATABASE_URL": db_url, "OUTPUT_DIR": str(output_dir)}),
        patch("kayak.cli.fetch.load_sources", return_value=yaml_sources),
        patch("kayak.cli.fetch.sync_sources", return_value=0),
        patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc", return_value=None),
    ):
        try:
            pipeline(args)
        finally:
            engine_mod.reset()

    index = output_dir / "index.html"
    assert index.is_file(), f"expected {index} to exist"
    html = index.read_text()
    assert fixture.reach_name in html, (
        f"{fixture.parser_id}: reach name {fixture.reach_name!r} missing from index.html"
    )
    assert fixture.expected_value in html, (
        f"{fixture.parser_id}: expected value {fixture.expected_value!r} missing from index.html"
    )
