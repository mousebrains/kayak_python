"""End-to-end integration test for the pipeline CLI.

Unlike ``test_pipeline.py`` (which mocks every stage to verify wiring), this
test seeds a real SQLite DB, feeds the fetch step a canned USGS RDB response
via ``input_dir``, then runs fetch → calc-rating → calculator → build and
asserts the generated ``index.html`` contains the seeded reach.

Catches regressions that individual-stage tests miss: schema / model / parser
drift, cache-update ordering, build HTML changes.

Marked ``slow`` and ``integration`` because the build step shells out to
``setfacl`` which isn't available on macOS dev machines. Run locally with
``pytest -m slow`` or in CI which uses the default selection (everything).
"""

from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event

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


def _build_rdb() -> str:
    """Minimal USGS RDB fixture with two rows timestamped near "now".

    The build step filters reaches whose latest observation is older than
    DATA_EXPIRY_THRESHOLD (7 days), so the timestamps must be recent for the
    seeded reach to appear in index.html. Generated relative to ``now`` so the
    test stays stable as wall-clock time advances.
    """
    now = datetime.now(UTC).replace(microsecond=0, second=0)
    t1 = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
    t2 = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    return (
        "# USGS RDB fixture\n"
        "#\n"
        "agency_cd\tsite_no\tdatetime\ttz_cd\t12345_00060\t12345_00060_cd\n"
        "5s\t15s\t20d\t6s\t14n\t10s\n"
        f"USGS\t12345678\t{t1}\tUTC\t500.0\tP\n"
        f"USGS\t12345678\t{t2}\tUTC\t525.0\tP\n"
    )


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

        # Flow range so the build step classifies the observation as "okay".
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


@pytest.mark.slow
@pytest.mark.integration
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
    rdb_path.write_text(_build_rdb())

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
