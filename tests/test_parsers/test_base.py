"""Tests for the BaseParser abstract class."""

from datetime import UTC, datetime

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.base import BaseParser


class ConcreteParser(BaseParser):
    """Minimal concrete parser for testing the base class."""

    name = "test"
    _seq = 0

    def parse_line(self, line):
        if line.startswith("DATA:"):
            ConcreteParser._seq += 1
            ts = datetime(2026, 1, 1, 12, ConcreteParser._seq, tzinfo=UTC)
            self.dump_to_db("test_station", DataType.flow, ts, 100.0)
        return True


class StopParser(BaseParser):
    """Parser that stops after encountering STOP."""

    name = "stop_test"
    _seq = 0

    def parse_line(self, line):
        if line == "STOP":
            return False
        if line.startswith("DATA:"):
            StopParser._seq += 1
            ts = datetime(2026, 1, 1, 13, StopParser._seq, tzinfo=UTC)
            self.dump_to_db("test_station", DataType.flow, ts, 50.0)
        return True


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
        """Matching lines should increment _db_updates and store observations."""
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


class TestParseLineStop:
    def test_returning_false_stops_processing(self, session):
        """parse_line returning False should stop further processing."""
        src = _make_source(session)
        parser = StopParser(url="https://example.com/test", session=session, source_id=src.id)
        count = parser.parse("DATA: one\nSTOP\nDATA: two\n")
        # Only the first DATA line is processed; STOP halts before DATA two
        assert count == 1


class TestStripHtml:
    def test_removes_tags(self):
        """_strip_html should remove HTML tags."""
        assert BaseParser._strip_html("<b>hello</b>") == "hello"

    def test_decodes_entities(self):
        """_strip_html should decode HTML entities."""
        assert BaseParser._strip_html("&amp;") == "&"
        assert BaseParser._strip_html("&lt;tag&gt;") == "<tag>"


class TestParseCooked:
    def test_strips_html_then_parses(self, session):
        """parse_cooked should strip HTML before parsing lines."""
        src = _make_source(session)
        parser = ConcreteParser(url="https://example.com/test", session=session, source_id=src.id)
        count = parser.parse_cooked("<p>DATA: value</p>")
        assert count == 1


class TestDumpToDbNoSource:
    def test_no_source_id_logs_error(self, session):
        """dump_to_db with source_id=None should log error and return False."""
        parser = ConcreteParser(url="https://example.com/test", session=session, source_id=None)
        result = parser.dump_to_db("station", DataType.flow, datetime.now(UTC), 100.0)
        assert result is False
        # _db_updates is still incremented before the check
        assert parser._db_updates == 1


class TestAutoCreateSource:
    def test_unknown_station_with_fetch_url_id_creates_source(self, session):
        """Unknown station with fetch_url_id set should auto-create Source and store."""
        fu = FetchUrl(url="https://example.com/auto", parser="test", is_active=True)
        session.add(fu)
        session.flush()

        parser = ConcreteParser(
            url="https://example.com/auto",
            session=session,
            fetch_url_id=fu.id,
            agency="test_agency",
        )
        result = parser.dump_to_db("NEW_STATION", DataType.flow, datetime.now(UTC), 42.0)
        assert result is True

        # Source was created and cached in source_map
        assert "NEW_STATION" in parser.source_map
        new_src = session.query(Source).filter_by(name="NEW_STATION").one()
        assert new_src.agency == "test_agency"
        assert new_src.fetch_url_id == fu.id
        assert parser.source_map["NEW_STATION"] == new_src.id

    def test_auto_created_source_reused_on_second_call(self, session):
        """Second call for the same station should reuse the cached source_map entry."""
        fu = FetchUrl(url="https://example.com/auto2", parser="test", is_active=True)
        session.add(fu)
        session.flush()

        parser = ConcreteParser(
            url="https://example.com/auto2",
            session=session,
            fetch_url_id=fu.id,
            agency="test_agency",
        )
        parser.dump_to_db("STN", DataType.flow, datetime.now(UTC), 1.0)
        parser.dump_to_db("STN", DataType.gauge, datetime.now(UTC), 2.0)

        # Only one Source should exist
        sources = session.query(Source).filter_by(name="STN").all()
        assert len(sources) == 1

    def test_no_fetch_url_id_still_logs_error(self, session):
        """Without fetch_url_id, unknown station should still log error."""
        parser = ConcreteParser(url="https://example.com/test", session=session, source_id=None)
        result = parser.dump_to_db("MISSING", DataType.flow, datetime.now(UTC), 100.0)
        assert result is False
