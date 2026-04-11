"""Tests for kayak.cli.fetch helpers."""

from unittest import mock

from kayak.cli.fetch import _get_content, _get_content_from_file, _hour_allowed

# ---------------------------------------------------------------------------
# _hour_allowed
# ---------------------------------------------------------------------------


def test_hour_allowed_empty_string():
    """Empty hours spec allows all hours."""
    assert _hour_allowed("") is True


def test_hour_allowed_matching_hour():
    """Returns True when the current UTC hour is in the spec."""
    from datetime import UTC, datetime

    current_hour = datetime.now(UTC).hour
    assert _hour_allowed(str(current_hour)) is True


def test_hour_allowed_non_matching_hour():
    """Returns False when the current UTC hour is not in the spec."""
    from datetime import UTC, datetime

    current_hour = datetime.now(UTC).hour
    other_hour = (current_hour + 12) % 24
    assert _hour_allowed(str(other_hour)) is False


def test_hour_allowed_invalid_spec():
    """Invalid (non-integer) spec returns True (fail-open)."""
    assert _hour_allowed("abc,xyz") is True


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
# fetch() command — dry run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_commit():
    """In dry-run mode the parse/store session is rolled back, not committed.

    Phase 1 (sync_sources) commits normally; Phase 3 (parse/store) rolls back.
    """
    import argparse

    from kayak.cli.fetch import fetch as fetch_cmd

    args = argparse.Namespace(
        dry_run=True,
        input_dir=None,
        single_url=None,
        parser_type=None,
        parser_filter=None,
        url_filter=None,
        url_prefix="",
        output_dir=None,
        show_name=False,
        fetch_only=False,
        ignore_constraints=False,
        concurrency=8,
    )

    phase1_session = mock.MagicMock()
    phase3_session = mock.MagicMock()
    sessions = iter([phase1_session, phase3_session])
    with (
        mock.patch("kayak.cli.fetch.load_sources", return_value=[]),
        mock.patch("kayak.cli.fetch.ensure_all_loaded"),
        mock.patch("kayak.cli.fetch.get_session", side_effect=lambda: next(sessions)),
    ):
        fetch_cmd(args)

    # Phase 1 commits sync_sources; Phase 3 rolls back in dry-run
    phase1_session.commit.assert_called_once()
    phase3_session.commit.assert_not_called()
    phase3_session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# fetch() command — concurrent fetch path
# ---------------------------------------------------------------------------


def test_fetch_uses_async_fetch_many():
    """fetch() calls async_fetch_many for the network path."""
    import argparse

    from kayak.cli.fetch import fetch as fetch_cmd
    from kayak.utils.http_client import FetchResult

    args = argparse.Namespace(
        dry_run=True,
        input_dir=None,
        single_url=None,
        parser_type=None,
        parser_filter=None,
        url_filter=None,
        url_prefix="",
        output_dir=None,
        show_name=False,
        fetch_only=True,
        ignore_constraints=True,
        concurrency=4,
    )

    sources = [{"url": "https://example.com/data", "parser": "test_parser", "hours": ""}]

    fake_result = FetchResult(url="https://example.com/data")
    fake_result.error = None
    fake_result._response = mock.MagicMock()
    fake_result._response.status_code = 200
    fake_result._response.text = "test-data"

    async def mock_async_fetch(urls, concurrency_per_host=8, timeout=None):
        return {url: fake_result for url in urls}

    phase1_session = mock.MagicMock()
    phase3_session = mock.MagicMock()
    sessions = iter([phase1_session, phase3_session])
    with (
        mock.patch("kayak.cli.fetch.load_sources", return_value=sources),
        mock.patch("kayak.cli.fetch.ensure_all_loaded"),
        mock.patch("kayak.cli.fetch.get_session", side_effect=lambda: next(sessions)),
        mock.patch("kayak.utils.http_client.async_fetch_many", mock_async_fetch),
    ):
        fetch_cmd(args)

    # Phase 3: dry_run → rollback, not commit
    phase3_session.commit.assert_not_called()
