"""End-to-end integration test for the pipeline CLI.

Unlike ``test_pipeline.py`` (which mocks every stage to verify wiring), this
test seeds a real SQLite DB, feeds the fetch step a canned USGS RDB response
via ``input_dir``, then runs fetch → calc-rating → calculator → build and
asserts the generated ``index.html`` contains the seeded reach.

Catches regressions that individual-stage tests miss: schema / model / parser
drift, cache-update ordering, build HTML changes.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event

from kayak.cli.pipeline import pipeline
from kayak.db import engine as engine_mod
from kayak.db.models import (
    Base,
    FetchUrl,
    Gauge,
    GaugeSource,
    Reach,
    ReachLevel,
    ReachState,
    Source,
    State,
)

# Minimal USGS RDB response: header + width line + two data rows with flow
# (parameter code 00060) sampled an hour apart. The parser keys on the column
# name ``TS_ID_00060`` — ``TS_ID`` is the time-series id; ``_00060`` tells the
# parser this column is cfs flow.
_RDB = """\
# USGS RDB fixture
#
agency_cd\tsite_no\tdatetime\ttz_cd\t12345_00060\t12345_00060_cd
5s\t15s\t20d\t6s\t14n\t10s
USGS\t12345678\t2026-04-20 12:00\tUTC\t500.0\tP
USGS\t12345678\t2026-04-20 13:00\tUTC\t525.0\tP
"""


def _seed(db_url: str) -> None:
    """Create schema + minimal fixtures against the on-disk test DB."""
    eng = create_engine(db_url)

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)

    from sqlalchemy.orm import Session

    with Session(eng) as s:
        s.add(State(name="Oregon", abbreviation="OR"))
        s.flush()

        fetch_url = FetchUrl(url="test://usgs", parser="usgs", is_active=True)
        s.add(fetch_url)
        s.flush()

        source = Source(name="12345678", agency="USGS", fetch_url_id=fetch_url.id)
        s.add(source)
        s.flush()

        gauge = Gauge(name="test_gauge", usgs_id="12345678")
        s.add(gauge)
        s.flush()
        s.add(GaugeSource(gauge_id=gauge.id, source_id=source.id))

        reach = Reach(
            name="test_reach",
            display_name="Test Creek",
            sort_name="Test Creek",
            gauge_id=gauge.id,
        )
        s.add(reach)
        s.flush()

        or_id = s.execute(
            __import__("sqlalchemy").select(State.id).where(State.abbreviation == "OR")
        ).scalar_one()
        s.add(ReachState(reach_id=reach.id, state_id=or_id))

        # Flow ranges so the build step classifies the observation as "okay".
        s.add_all(
            [
                ReachLevel(reach_id=reach.id, level="low", high=200.0, high_data_type="flow"),
                ReachLevel(
                    reach_id=reach.id,
                    level="okay",
                    low=200.0,
                    low_data_type="flow",
                    high=800.0,
                    high_data_type="flow",
                ),
                ReachLevel(reach_id=reach.id, level="high", low=800.0, low_data_type="flow"),
            ]
        )
        s.commit()


def test_pipeline_fetch_through_build_smoke(tmp_path: Path) -> None:
    """Full pipeline: fetch canned RDB → build → rendered reach name in index.html."""
    db_path = tmp_path / "kayak.db"
    db_url = f"sqlite:///{db_path}"
    _seed(db_url)

    # Fetch reads from input_dir when args.input_dir is set; the file path is
    # resolved as ``input_dir / raw_url.lstrip('/')``. For the url
    # ``test://usgs`` the path becomes ``input_dir/test:/usgs``.
    input_dir = tmp_path / "input"
    rdb_path = input_dir / "test:" / "usgs"
    rdb_path.parent.mkdir(parents=True)
    rdb_path.write_text(_RDB)

    output_dir = tmp_path / "out"

    args = Namespace(
        # pipeline + fetch args
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

    # Point the shared engine module at the test DB for the duration of the run.
    engine_mod.reset()
    engine_mod.get_engine(db_url)
    engine_mod.get_session_factory(db_url)

    # One-entry source list matching our test FetchUrl so fetch() doesn't
    # iterate all 100+ sources from the real data/sources.yaml.
    yaml_sources = [{"url": "test://usgs", "parser": "usgs", "hours": ""}]

    # OUTPUT_DIR override routes build()'s path lookup onto our tmp dir.
    # Stub fetch-usgs-ogc to avoid hitting the real USGS OGC endpoint.
    with (
        patch.dict("os.environ", {"DATABASE_URL": db_url, "OUTPUT_DIR": str(output_dir)}),
        patch("kayak.cli.fetch.load_sources", return_value=yaml_sources),
        patch("kayak.cli.pipeline.fetch_usgs_ogc.fetch_usgs_ogc", return_value=None),
    ):
        try:
            pipeline(args)
        finally:
            engine_mod.reset()

    index = output_dir / "index.html"
    assert index.is_file(), f"expected {index} to exist"
    html = index.read_text()
    assert "Test Creek" in html
    # The fetch delivered a flow of 525 cfs (most recent); the build step
    # should land somewhere in the HTML.
    assert "525" in html
