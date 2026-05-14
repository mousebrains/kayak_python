"""Tests for kayak.cli.orphan_check."""

import json
from argparse import Namespace
from datetime import UTC, datetime
from unittest.mock import patch

from kayak.cli.orphan_check import orphan_check
from kayak.db.models import DataType, FetchUrl, Gauge, GaugeSource, LatestObservation, Source


def _seed_orphan(session) -> int:
    fu = FetchUrl(url="https://example.com/orphan", parser="nwps", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name="orphan-st", fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    session.add(
        LatestObservation(
            source_id=src.id,
            data_type=DataType.flow,
            observed_at=datetime.now(UTC).replace(tzinfo=None),
            value=42.0,
        )
    )
    session.flush()
    session.commit()
    return src.id


def _seed_linked(session) -> int:
    fu = FetchUrl(url="https://example.com/linked", parser="nwps", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name="linked-st", fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    gauge = Gauge(name="g")
    session.add(gauge)
    session.flush()
    session.add(GaugeSource(gauge_id=gauge.id, source_id=src.id))
    session.flush()
    session.commit()
    return src.id


def _run(args_overrides=None):
    """Run orphan_check with the patched get_session, return SystemExit code (or None)."""
    args = Namespace(as_json=False, exit_nonzero_if_found=False)
    if args_overrides:
        for k, v in args_overrides.items():
            setattr(args, k, v)
    try:
        orphan_check(args)
    except SystemExit as e:
        return e.code
    return None


class TestOrphanCheckCli:
    def test_no_orphans_prints_clean_message(self, session, capsys):
        _seed_linked(session)
        with patch("kayak.cli.orphan_check.get_session", return_value=session):
            code = _run()
        out = capsys.readouterr().out
        assert "No orphan sources." in out
        assert code is None

    def test_orphan_prints_table(self, session, capsys):
        src_id = _seed_orphan(session)
        with patch("kayak.cli.orphan_check.get_session", return_value=session):
            _run()
        out = capsys.readouterr().out
        assert str(src_id) in out
        assert "orphan-st" in out
        assert "https://example.com/orphan" in out
        assert "1 orphan source(s)." in out

    def test_json_output_is_valid_json(self, session, capsys):
        _seed_orphan(session)
        with patch("kayak.cli.orphan_check.get_session", return_value=session):
            _run({"as_json": True})
        out = capsys.readouterr().out
        data = json.loads(out)
        assert len(data) == 1
        row = data[0]
        assert row["name"] == "orphan-st"
        assert row["url"] == "https://example.com/orphan"
        assert row["is_active"] is True
        assert row["latest_obs"] is not None

    def test_exit_nonzero_when_orphans_and_flag_set(self, session):
        _seed_orphan(session)
        with patch("kayak.cli.orphan_check.get_session", return_value=session):
            code = _run({"exit_nonzero_if_found": True})
        assert code == 1

    def test_exit_zero_when_no_orphans_even_with_flag(self, session):
        _seed_linked(session)
        with patch("kayak.cli.orphan_check.get_session", return_value=session):
            code = _run({"exit_nonzero_if_found": True})
        assert code is None  # no SystemExit raised
