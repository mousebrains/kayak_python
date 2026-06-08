"""Tests for the BaseParser abstract class."""

from datetime import UTC, datetime

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.base import BaseParser, ObservationRecord


class ConcreteParser(BaseParser):
    """Minimal concrete parser for testing the base class.

    Emits one record per ``DATA:`` line. The base ``parse()`` wrapper
    is what we're exercising — these tests stay focused on it.
    """

    name = "test"
    _seq = 0

    def parse_records(self, text):
        records = []
        for line in text.splitlines():
            if line.startswith("DATA:"):
                ConcreteParser._seq += 1
                ts = datetime(2026, 1, 1, 12, ConcreteParser._seq, tzinfo=UTC)
                records.append(ObservationRecord("test_station", DataType.flow, ts, 100.0))
        return records


def _make_source(session, name="base_test"):
    fu = FetchUrl(url=f"https://example.com/{name}", parser="test", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


class TestParseEmpty:
    def test_parse_empty_string(self, session):
        """Parsing empty string should return 0 updates."""
        src = _make_source(session)
        parser = ConcreteParser(url="https://example.com/test", session=session, source_id=src.id)
        assert parser.parse("") == 0

    def test_parse_no_matching_lines(self, session):
        """Lines that don't match should produce 0 updates and log a warning."""
        src = _make_source(session)
        parser = ConcreteParser(url="https://example.com/test", session=session, source_id=src.id)
        assert parser.parse("nothing here\njust text\n") == 0


class TestParseWithData:
    def test_parse_matching_lines_increments(self, session):
        """Matching records should increment _db_updates and store observations."""
        src = _make_source(session)
        parser = ConcreteParser(url="https://example.com/test", session=session, source_id=src.id)
        count = parser.parse("DATA: one\nDATA: two\n")
        assert count == 2

        rows = session.query(Observation).filter_by(source_id=src.id).all()
        assert len(rows) == 2

    def test_dry_run_counts_without_storing(self, session):
        """dry_run=True should count updates but not write to DB."""
        src = _make_source(session)
        parser = ConcreteParser(
            url="https://example.com/test", session=session, source_id=src.id, dry_run=True
        )
        count = parser.parse("DATA: one\nDATA: two\n")
        assert count == 2
        assert session.query(Observation).count() == 0


class TestStripHtml:
    def test_removes_tags(self):
        """_strip_html should remove HTML tags."""
        assert BaseParser._strip_html("<b>hello</b>") == "hello"

    def test_decodes_entities(self):
        """_strip_html should decode HTML entities."""
        assert BaseParser._strip_html("&amp;") == "&"
        assert BaseParser._strip_html("&lt;tag&gt;") == "<tag>"


class TestDumpToDbNoSource:
    def test_no_source_records_unknown_station(self, session):
        """dump_to_db with no resolvable source drops the obs (returns False) and
        records the station for the fetch driver's policy — it never auto-creates
        a Source (dataset-separation S1)."""
        parser = ConcreteParser(url="https://example.com/test", session=session, source_id=None)
        result = parser.dump_to_db("station", DataType.flow, datetime.now(UTC), 100.0)
        assert result is False
        # _db_updates is still incremented before the resolve check
        assert parser._db_updates == 1
        assert parser.unknown_stations == {"station"}
        assert parser.dropped_obs_count == 1


class TestSourceTzLocalization:
    """dump_to_db localizes naive datetimes when source_tz_map has the station."""

    def test_naive_with_tz_map_converts_to_utc(self, session):
        src = _make_source(session, name="STN_MT")
        parser = ConcreteParser(
            url="https://example.com/test",
            session=session,
            source_map={"STN_MT": src.id},
            source_tz_map={"STN_MT": "America/Boise"},
        )
        # 09:15 MDT (DST active in March 2026) = 15:15 UTC
        naive = datetime(2026, 3, 15, 9, 15)
        parser.dump_to_db("STN_MT", DataType.flow, naive, 100.0)
        parser._flush_buffer()
        obs = session.query(Observation).filter_by(source_id=src.id).one()
        assert obs.observed_at.replace(tzinfo=UTC) == datetime(2026, 3, 15, 15, 15, tzinfo=UTC)

    def test_naive_with_tz_map_winter_standard_time(self, session):
        src = _make_source(session, name="STN_PT")
        parser = ConcreteParser(
            url="https://example.com/test",
            session=session,
            source_map={"STN_PT": src.id},
            source_tz_map={"STN_PT": "America/Los_Angeles"},
        )
        # 09:15 PST (winter) = 17:15 UTC
        naive = datetime(2026, 1, 15, 9, 15)
        parser.dump_to_db("STN_PT", DataType.flow, naive, 100.0)
        parser._flush_buffer()
        obs = session.query(Observation).filter_by(source_id=src.id).one()
        assert obs.observed_at.replace(tzinfo=UTC) == datetime(2026, 1, 15, 17, 15, tzinfo=UTC)

    def test_fixed_offset_tz_no_dst(self, session):
        src = _make_source(session, name="WA_STN")
        parser = ConcreteParser(
            url="https://example.com/test",
            session=session,
            source_map={"WA_STN": src.id},
            source_tz_map={"WA_STN": "Etc/GMT+8"},  # PST year-round
        )
        # Etc/GMT+8 is UTC-8 (POSIX quirk). 09:15 local = 17:15 UTC in DST-active
        # (March) AND standard (January) — the whole point of fixed offsets.
        dst_active = datetime(2026, 3, 15, 9, 15)
        standard = datetime(2026, 1, 15, 9, 15)
        parser.dump_to_db("WA_STN", DataType.flow, dst_active, 1.0)
        parser.dump_to_db("WA_STN", DataType.gauge, standard, 2.0)
        parser._flush_buffer()
        rows = {
            o.data_type: o.observed_at
            for o in session.query(Observation).filter_by(source_id=src.id).all()
        }
        assert rows[DataType.flow].replace(tzinfo=UTC) == datetime(2026, 3, 15, 17, 15, tzinfo=UTC)
        assert rows[DataType.gauge].replace(tzinfo=UTC) == datetime(2026, 1, 15, 17, 15, tzinfo=UTC)

    def test_naive_without_tz_map_stored_as_utc(self, session):
        """Without source_tz_map entry, naive timestamps get UTC stamp at store."""
        src = _make_source(session, name="STN_UTC")
        parser = ConcreteParser(
            url="https://example.com/test",
            session=session,
            source_map={"STN_UTC": src.id},
        )
        naive = datetime(2026, 3, 15, 9, 15)
        parser.dump_to_db("STN_UTC", DataType.flow, naive, 100.0)
        parser._flush_buffer()
        obs = session.query(Observation).filter_by(source_id=src.id).one()
        assert obs.observed_at.replace(tzinfo=UTC) == datetime(2026, 3, 15, 9, 15, tzinfo=UTC)

    def test_tz_aware_input_bypasses_localization(self, session):
        """Already-tz-aware datetimes are stored verbatim (parser already did the work)."""
        src = _make_source(session, name="STN_AWARE")
        parser = ConcreteParser(
            url="https://example.com/test",
            session=session,
            source_map={"STN_AWARE": src.id},
            source_tz_map={"STN_AWARE": "America/Boise"},  # would shift if applied
        )
        aware_utc = datetime(2026, 3, 15, 15, 15, tzinfo=UTC)
        parser.dump_to_db("STN_AWARE", DataType.flow, aware_utc, 100.0)
        parser._flush_buffer()
        obs = session.query(Observation).filter_by(source_id=src.id).one()
        assert obs.observed_at.replace(tzinfo=UTC) == aware_utc


class TestUnknownStation:
    """An undeclared station (no source row) is dropped and recorded — fetch no
    longer auto-creates Source rows at run time (dataset-separation S1). Known
    sibling stations on the same feed are still saved (partial-save)."""

    def test_unknown_station_dropped_not_created(self, session):
        fu = FetchUrl(url="https://example.com/auto", parser="test", is_active=True)
        session.add(fu)
        session.flush()

        parser = ConcreteParser(url="https://example.com/auto", session=session, fetch_url_id=fu.id)
        result = parser.dump_to_db("NEW_STATION", DataType.flow, datetime.now(UTC), 42.0)

        assert result is False
        assert session.query(Source).filter_by(name="NEW_STATION").one_or_none() is None
        assert "NEW_STATION" not in parser.source_map
        assert parser.unknown_stations == {"NEW_STATION"}
        assert parser.dropped_obs_count == 1

    def test_dropped_count_accumulates(self, session):
        """Every dropped obs from an unknown station bumps the counter; the
        station appears once in the set."""
        parser = ConcreteParser(url="https://example.com/x", session=session, source_id=None)
        parser.dump_to_db("STN", DataType.flow, datetime.now(UTC), 1.0)
        parser.dump_to_db("STN", DataType.gauge, datetime.now(UTC), 2.0)

        assert parser.unknown_stations == {"STN"}
        assert parser.dropped_obs_count == 2
        assert session.query(Source).filter_by(name="STN").all() == []

    def test_partial_save_keeps_known_sibling(self, session):
        """The owner's case: a multi-station feed with one known + one unknown
        station saves the known station's obs and drops only the unknown."""
        fu = FetchUrl(url="https://example.com/multi", parser="test", is_active=True)
        session.add(fu)
        session.flush()
        known = Source(name="KNOWN", fetch_url_id=fu.id)
        session.add(known)
        session.flush()

        class TwoStationParser(BaseParser):
            name = "test"

            def parse_records(self, text):
                ts = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
                return [
                    ObservationRecord("KNOWN", DataType.flow, ts, 10.0),
                    ObservationRecord("MYSTERY", DataType.flow, ts, 20.0),
                ]

        parser = TwoStationParser(
            url="https://example.com/multi",
            session=session,
            source_map={"KNOWN": known.id},
            fetch_url_id=fu.id,
        )
        parser.parse("go")
        session.flush()

        obs = session.query(Observation).filter_by(source_id=known.id).all()
        assert len(obs) == 1 and obs[0].value == 10.0  # known sibling saved
        assert parser.unknown_stations == {"MYSTERY"}  # unknown dropped + recorded
        assert parser.dropped_obs_count == 1
        assert session.query(Source).filter_by(name="MYSTERY").one_or_none() is None

    def test_parse_resets_unknown_state_per_call(self, session):
        """A reused parser instance clears unknown-station state on each parse()."""
        parser = ConcreteParser(url="https://example.com/r", session=session, source_id=None)
        parser.parse("DATA:1")  # test_station has no source → recorded
        assert parser.unknown_stations == {"test_station"}

        src = _make_source(session, name="test_station")
        parser.source_map = {"test_station": src.id}
        parser.parse("DATA:1")  # now resolvable → state reset, nothing unknown
        assert parser.unknown_stations == set()
        assert parser.dropped_obs_count == 0
