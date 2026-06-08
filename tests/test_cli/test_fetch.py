"""Tests for kayak.cli.fetch helpers."""

from types import SimpleNamespace
from unittest import mock

import pytest

from kayak.cli.fetch import _get_content, _get_content_from_file, _hour_allowed, _safe_subpath

# ---------------------------------------------------------------------------
# _hour_allowed
# ---------------------------------------------------------------------------


def test_hour_allowed_empty_string():
    """Empty hours spec allows all hours."""
    assert _hour_allowed("") is True


def test_hour_allowed_matching_hour():
    """Returns True when the current UTC hour is in the spec."""
    from datetime import UTC, datetime

    fixed = datetime(2026, 4, 20, 15, 30, tzinfo=UTC)
    assert _hour_allowed("15", now=fixed) is True


def test_hour_allowed_non_matching_hour():
    """Returns False when the current UTC hour is not in the spec."""
    from datetime import UTC, datetime

    fixed = datetime(2026, 4, 20, 15, 30, tzinfo=UTC)
    assert _hour_allowed("3", now=fixed) is False


def test_hour_allowed_invalid_spec():
    """Invalid (non-integer) spec returns False (fail-closed).

    A garbled hours column in fetch_url / sources.yaml would silently
    allow-always under the old fail-open behavior; flipping to False
    ensures a data-entry typo surfaces as "nothing fetched at this hour"
    rather than "fetched every hour".
    """
    assert _hour_allowed("abc,xyz") is False


# ---------------------------------------------------------------------------
# _safe_subpath
# ---------------------------------------------------------------------------


class TestSafeSubpath:
    def test_normal_url_resolves(self, tmp_path):
        result = _safe_subpath(tmp_path, "data/feed.txt")
        assert str(result).startswith(str(tmp_path))

    def test_traversal_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Path traversal"):
            _safe_subpath(tmp_path, "../../etc/passwd")

    def test_double_dot_in_middle_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="Path traversal"):
            _safe_subpath(tmp_path, "data/../../etc/passwd")

    def test_absolute_path_stays_within_base(self, tmp_path):
        result = _safe_subpath(tmp_path, "/data/file.txt")
        assert str(result).startswith(str(tmp_path))

    def test_leading_slashes_stripped(self, tmp_path):
        result = _safe_subpath(tmp_path, "///data/file.txt")
        assert result == tmp_path / "data/file.txt"


# ---------------------------------------------------------------------------
# _get_content_from_file
# ---------------------------------------------------------------------------


def test_get_content_from_file_reads(tmp_path):
    """_get_content_from_file reads from input_dir when file exists."""
    sub = tmp_path / "data"
    sub.mkdir()
    data_file = sub / "feed.txt"
    data_file.write_text("line1\nline2", encoding="utf-8")

    result = _get_content_from_file("data/feed.txt", str(tmp_path))
    assert result == "line1\nline2"


def test_get_content_from_file_missing(tmp_path):
    """_get_content_from_file returns None when file does not exist."""
    result = _get_content_from_file("nofile.txt", str(tmp_path))
    assert result is None


# ---------------------------------------------------------------------------
# _get_content (used by _fetch_single)
# ---------------------------------------------------------------------------


def test_get_content_from_input_dir(tmp_path):
    """_get_content reads from input_dir when provided."""
    sub = tmp_path / "data"
    sub.mkdir()
    data_file = sub / "feed.txt"
    data_file.write_text("line1\nline2", encoding="utf-8")

    result = _get_content(
        url="https://example.com/data/feed.txt",
        raw_url="data/feed.txt",
        input_dir=str(tmp_path),
        output_dir=None,
    )
    assert result == "line1\nline2"


def test_get_content_missing_input_file(tmp_path):
    """_get_content returns None when the saved file does not exist."""
    result = _get_content(
        url="https://example.com/nofile.txt",
        raw_url="nofile.txt",
        input_dir=str(tmp_path),
        output_dir=None,
    )
    assert result is None


def test_get_content_from_url():
    """_get_content fetches from URL when input_dir is None."""
    fake_result = mock.MagicMock()
    fake_result.ok = True
    fake_result.status_code = 200
    fake_result.text = "remote-data"

    with mock.patch("kayak.utils.http_client.fetch", return_value=fake_result):
        result = _get_content(
            url="https://example.com/feed",
            raw_url="feed",
            input_dir=None,
            output_dir=None,
        )
    assert result == "remote-data"


def test_get_content_url_error():
    """_get_content returns None when the HTTP fetch reports an error."""
    fake_result = mock.MagicMock()
    fake_result.ok = False
    fake_result.error = "connection refused"

    with mock.patch("kayak.utils.http_client.fetch", return_value=fake_result):
        result = _get_content(
            url="https://example.com/down",
            raw_url="down",
            input_dir=None,
            output_dir=None,
        )
    assert result is None


def test_get_content_url_http_error():
    """_get_content returns None on HTTP 4xx/5xx status codes."""
    fake_result = mock.MagicMock()
    fake_result.ok = True
    fake_result.error = None
    fake_result.status_code = 404

    with mock.patch("kayak.utils.http_client.fetch", return_value=fake_result):
        result = _get_content(
            url="https://example.com/missing",
            raw_url="missing",
            input_dir=None,
            output_dir=None,
        )
    assert result is None


def test_get_content_saves_to_output_dir(tmp_path):
    """_get_content writes fetched data to output_dir when specified."""
    fake_result = mock.MagicMock()
    fake_result.ok = True
    fake_result.status_code = 200
    fake_result.text = "saved-data"

    with mock.patch("kayak.utils.http_client.fetch", return_value=fake_result):
        result = _get_content(
            url="https://example.com/data/file.txt",
            raw_url="data/file.txt",
            input_dir=None,
            output_dir=str(tmp_path),
        )

    assert result == "saved-data"
    fake_result.write_file.assert_called_once()


# ---------------------------------------------------------------------------
# fetch() command — DB-driven work-list (S1: no sources.yaml, no sync_sources)
# ---------------------------------------------------------------------------


def _args(**overrides):
    """A fetch argparse.Namespace with sensible defaults, overridable per test."""
    import argparse

    base = dict(
        dry_run=False,
        input_dir=None,
        single_url=None,
        parser_type=None,
        parser_filter=None,
        url_filter=None,
        url_prefix="",
        output_dir=None,
        show_name=False,
        fetch_only=False,
        ignore_constraints=True,
        concurrency=4,
        budget=0,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _src(name, sid, tz=None):
    return SimpleNamespace(name=name, id=sid, timezone=tz)


def _fu(fid, url, parser="p", hours="", policy=None, sources=()):
    """A stand-in for a FetchUrl row as returned by get_active_fetch_urls
    (with its sources eager-loaded)."""
    return SimpleNamespace(
        id=fid,
        url=url,
        parser=parser,
        hours=hours,
        is_active=True,
        unknown_station_policy=policy,
        sources=list(sources),
    )


def _result(url, text="data"):
    from kayak.utils.http_client import FetchResult

    r = FetchResult(url=url)
    r.error = None
    r._response = mock.MagicMock()
    r._response.status_code = 200
    r._response.text = text
    return r


def _run_fetch(args, fetch_urls, parser_for, content_text="data"):
    """Drive fetch() against a faked DB work-list + network, returning its rc.

    Mocks get_active_fetch_urls (the DB read), get_session (two MagicMock
    sessions), the async network, and get_parser_class. ``parser_for`` maps a
    parser name → a parser class (or None)."""
    from kayak.cli.fetch import fetch as fetch_cmd

    async def mock_async_fetch(urls, **_kw):
        return {u: _result(u, content_text) for u in urls}

    sessions = iter([mock.MagicMock(), mock.MagicMock()])
    with (
        mock.patch("kayak.cli.fetch.ensure_all_loaded"),
        mock.patch("kayak.cli.fetch.get_active_fetch_urls", return_value=fetch_urls),
        mock.patch("kayak.cli.fetch.get_session", side_effect=lambda: next(sessions)),
        mock.patch("kayak.utils.http_client.async_fetch_many", mock_async_fetch),
        mock.patch("kayak.cli.fetch.get_parser_class", side_effect=parser_for),
    ):
        return fetch_cmd(args)


def test_dry_run_does_not_commit():
    """In dry-run mode the parse/store session is rolled back, not committed, and
    fetch no longer opens a sync_sources write in Phase 1."""
    from kayak.cli.fetch import fetch as fetch_cmd

    phase1_session = mock.MagicMock()
    phase3_session = mock.MagicMock()
    sessions = iter([phase1_session, phase3_session])
    with (
        mock.patch("kayak.cli.fetch.ensure_all_loaded"),
        mock.patch("kayak.cli.fetch.get_active_fetch_urls", return_value=[]),
        mock.patch("kayak.cli.fetch.get_session", side_effect=lambda: next(sessions)),
    ):
        rc = fetch_cmd(_args(dry_run=True))

    assert rc == 0
    # Phase 1 is read-only now (no sync_sources commit); Phase 3 rolls back.
    phase1_session.commit.assert_not_called()
    phase3_session.commit.assert_not_called()
    phase3_session.rollback.assert_called_once()


def test_fetch_uses_async_fetch_many():
    """fetch() calls async_fetch_many for the network path (fetch-only here)."""
    captured: dict[str, object] = {}

    async def mock_async_fetch(urls, **kw):
        captured["urls"] = list(urls)
        return {u: _result(u) for u in urls}

    from kayak.cli.fetch import fetch as fetch_cmd

    sessions = iter([mock.MagicMock(), mock.MagicMock()])
    with (
        mock.patch("kayak.cli.fetch.ensure_all_loaded"),
        mock.patch(
            "kayak.cli.fetch.get_active_fetch_urls",
            return_value=[_fu(1, "https://example.com/data", parser="p")],
        ),
        mock.patch("kayak.cli.fetch.get_session", side_effect=lambda: next(sessions)),
        mock.patch("kayak.utils.http_client.async_fetch_many", mock_async_fetch),
    ):
        rc = fetch_cmd(_args(dry_run=True, fetch_only=True))

    assert rc == 0
    assert captured["urls"] == ["https://example.com/data"]


def test_fetch_continues_after_unexpected_parser_error():
    """A parser raising an unexpected exception (not Value/Key/LookupError)
    must NOT skip remaining URLs in the batch — the loop logs the traceback
    and moves on. Regression for the prior `except Exception: raise` that
    killed the entire run on a single transient failure."""
    parsed_urls: list[str] = []

    class _GoodParser:
        def __init__(self, **kw):
            self.url = kw["url"]
            self.unknown_stations: set[str] = set()
            self.dropped_obs_count = 0

        def parse(self, _text):
            parsed_urls.append(self.url)
            return 1

    class _BadParser(_GoodParser):
        def parse(self, _text):
            parsed_urls.append(self.url)
            raise RuntimeError("simulated parser bug")

    def parser_for(name):
        return _BadParser if name == "p_bad" else _GoodParser

    fetch_urls = [
        _fu(1, "https://example.com/bad", parser="p_bad", sources=[_src("B", 1)]),
        _fu(2, "https://example.com/good", parser="p_good", sources=[_src("G", 2)]),
    ]
    rc = _run_fetch(_args(), fetch_urls, parser_for)

    # A clean run (no undeclared stations) returns 0; both URLs were tried.
    assert rc == 0
    assert "https://example.com/bad" in parsed_urls, "bad URL never reached parser"
    assert "https://example.com/good" in parsed_urls, "good URL was skipped after bad URL raised"


# ---------------------------------------------------------------------------
# fetch() command — unknown-station policy (S1)
# ---------------------------------------------------------------------------


class _UnknownStationParser:
    """A parser that reports an undeclared station (as the base class would)."""

    def __init__(self, **kw):
        self.url = kw["url"]
        self.unknown_stations = {"MYSTERY"}
        self.dropped_obs_count = 3

    def parse(self, _text):
        return 3


def test_reject_policy_returns_nonzero():
    """Default (reject) policy: an undeclared station makes fetch exit non-zero
    so monitoring alerts — even though known siblings were saved."""
    fetch_urls = [_fu(1, "https://example.com/r", parser="p", policy=None)]
    rc = _run_fetch(_args(), fetch_urls, lambda _n: _UnknownStationParser)
    assert rc == 1


def test_ignore_policy_returns_zero():
    """unknown_station_policy=ignore: the undeclared station is dropped quietly,
    no non-zero exit."""
    fetch_urls = [_fu(1, "https://example.com/i", parser="p", policy="ignore")]
    rc = _run_fetch(_args(), fetch_urls, lambda _n: _UnknownStationParser)
    assert rc == 0


def test_clean_run_returns_zero():
    """No undeclared stations → exit 0."""

    class _Clean:
        def __init__(self, **kw):
            self.url = kw["url"]
            self.unknown_stations: set[str] = set()
            self.dropped_obs_count = 0

        def parse(self, _text):
            return 1

    fetch_urls = [_fu(1, "https://example.com/c", parser="p", sources=[_src("C", 1)])]
    rc = _run_fetch(_args(), fetch_urls, lambda _n: _Clean)
    assert rc == 0


def test_apply_unknown_station_policy_classification():
    """_apply_unknown_station_policy: reject (default/blank/typo) → True; the
    exact string 'ignore' (case/space-insensitive) → False."""
    from kayak.cli.fetch import _apply_unknown_station_policy, _FetchWork

    parser = _UnknownStationParser(url="https://example.com/x")

    def _verdict(policy):
        work = _FetchWork(
            url="u", raw_url="u", parser_name="p", source_id=None, unknown_station_policy=policy
        )
        return _apply_unknown_station_policy(work, parser)

    assert _verdict(None) is True  # default
    assert _verdict("") is True
    assert _verdict("reject") is True
    assert _verdict("bogus") is True  # fail-safe: anything but 'ignore' rejects
    assert _verdict("ignore") is False
    assert _verdict("  IGNORE  ") is False  # case/space-insensitive
