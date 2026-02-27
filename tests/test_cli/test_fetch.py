"""Tests for kayak.cli.fetch helpers."""

from unittest import mock

from kayak.cli.fetch import _get_content, _hour_allowed

# ---------------------------------------------------------------------------
# _hour_allowed
# ---------------------------------------------------------------------------


def test_hour_allowed_empty_string():
    """Empty hours spec allows all hours."""
    assert _hour_allowed("") is True


def test_hour_allowed_matching_hour():
    """Returns True when the current hour is in the spec."""
    from datetime import datetime

    current_hour = datetime.now().hour
    assert _hour_allowed(str(current_hour)) is True


def test_hour_allowed_non_matching_hour():
    """Returns False when the current hour is not in the spec."""
    from datetime import datetime

    current_hour = datetime.now().hour
    other_hour = (current_hour + 12) % 24
    assert _hour_allowed(str(other_hour)) is False


def test_hour_allowed_invalid_spec():
    """Invalid (non-integer) spec returns True (fail-open)."""
    assert _hour_allowed("abc,xyz") is True


# ---------------------------------------------------------------------------
# _get_content
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

    with (
        mock.patch("kayak.cli.fetch.http_fetch", create=True),
        mock.patch("kayak.utils.http_client.fetch", return_value=fake_result),
    ):
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


def test_dry_run_does_not_commit():
    """In dry-run mode the session is rolled back, not committed."""
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
    )

    mock_session = mock.MagicMock()
    with (
        mock.patch("kayak.cli.fetch.load_sources", return_value=[]),
        mock.patch("kayak.cli.fetch.ensure_all_loaded"),
        mock.patch("kayak.cli.fetch.get_session", return_value=mock_session),
    ):
        fetch_cmd(args)

    mock_session.commit.assert_not_called()
    mock_session.rollback.assert_called_once()


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
