"""Tests for the USACE Outflow parser."""

from kayak.db.models import DataType, FetchUrl, Observation, Source
from kayak.parsers.usace_outflow import USACEOutflowParser

USACE_BASIC = """\
  PROJECT- DALLES
  REPORT Jun 15 2024

  01  00  150.0
  02  00  145.0
  03  00  148.0
"""

USACE_PREFIX = """\
  PROJECT-DALLES
  REPORT Jun 15 2024

  01  00  120.0
"""

USACE_MISSING_REPORT = """\
  PROJECT- DALLES
  Some other line here
  01  00  150.0
"""

USACE_MULTI_PROJECT = """\
  PROJECT- DALLES
  REPORT Jun 15 2024

  01  00  150.0
  02  00  145.0

  PROJECT- BONNEVILLE
  REPORT Jun 15 2024

  04  00  200.0
"""


def _make_source(session, name="usace_test"):
    fu = FetchUrl(url=f"https://example.com/{name}", parser="usace.outflow", is_active=True)
    session.add(fu)
    session.flush()
    src = Source(name=name, fetch_url_id=fu.id)
    session.add(src)
    session.flush()
    return src


class TestUSACEBasic:
    def test_parse_outflow_multiplied_by_1000(self, session):
        """Values should be multiplied by 1000 (KCFS to CFS)."""
        src = _make_source(session)
        parser = USACEOutflowParser(
            url="https://example.com/usace", session=session, source_id=src.id
        )
        count = parser.parse(USACE_BASIC)

        assert count == 3

        flows = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.flow)
            .order_by(Observation.observed_at)
            .all()
        )
        assert len(flows) == 3
        assert flows[0].value == 150000.0
        assert flows[1].value == 145000.0
        assert flows[2].value == 148000.0

    def test_project_prefix_format(self, session):
        """PROJECT-NAME (without space) should also parse."""
        src = _make_source(session)
        parser = USACEOutflowParser(
            url="https://example.com/usace", session=session, source_id=src.id
        )
        count = parser.parse(USACE_PREFIX)
        assert count == 1

        flow = session.query(Observation).filter_by(source_id=src.id).first()
        assert flow.value == 120000.0


class TestUSACEEdgeCases:
    def test_missing_report_resets_state(self, session):
        """If REPORT line is missing after PROJECT-, state resets and data is skipped."""
        src = _make_source(session)
        parser = USACEOutflowParser(
            url="https://example.com/usace", session=session, source_id=src.id
        )
        count = parser.parse(USACE_MISSING_REPORT)
        assert count == 0

    def test_empty_input(self, session):
        """Empty input should return 0."""
        src = _make_source(session)
        parser = USACEOutflowParser(
            url="https://example.com/usace", session=session, source_id=src.id
        )
        assert parser.parse("") == 0

    def test_multiple_projects(self, session):
        """Multiple PROJECT blocks in the same text should all parse."""
        src = _make_source(session)
        parser = USACEOutflowParser(
            url="https://example.com/usace", session=session, source_id=src.id
        )
        count = parser.parse(USACE_MULTI_PROJECT)

        # 2 rows from DALLES + 1 row from BONNEVILLE = 3
        assert count == 3

        flows = (
            session.query(Observation)
            .filter_by(source_id=src.id, data_type=DataType.flow)
            .all()
        )
        assert len(flows) == 3
