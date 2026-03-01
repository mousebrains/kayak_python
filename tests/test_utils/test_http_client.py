"""Tests for kayak.utils.http_client."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from kayak.utils.http_client import FetchResult, fetch

# ---------------------------------------------------------------------------
# FetchResult with no response
# ---------------------------------------------------------------------------


class TestFetchResultNoResponse:
    def test_ok_is_false(self):
        result = FetchResult(url="http://example.com")
        assert result.ok is False

    def test_status_code_is_zero(self):
        result = FetchResult(url="http://example.com")
        assert result.status_code == 0

    def test_text_is_empty(self):
        result = FetchResult(url="http://example.com")
        assert result.text == ""

    def test_content_is_empty_bytes(self):
        result = FetchResult(url="http://example.com")
        assert result.content == b""

    def test_content_type_is_empty(self):
        result = FetchResult(url="http://example.com")
        assert result.content_type == ""


# ---------------------------------------------------------------------------
# FetchResult with mock response
# ---------------------------------------------------------------------------


class TestFetchResultWithResponse:
    @pytest.fixture()
    def mock_response(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 200
        resp.text = "hello"
        resp.content = b"hello"
        resp.headers = {"Content-Type": "text/plain"}
        return resp

    def test_ok_is_true(self, mock_response):
        result = FetchResult(url="http://example.com", response=mock_response)
        assert result.ok is True

    def test_status_code(self, mock_response):
        result = FetchResult(url="http://example.com", response=mock_response)
        assert result.status_code == 200

    def test_text(self, mock_response):
        result = FetchResult(url="http://example.com", response=mock_response)
        assert result.text == "hello"

    def test_content(self, mock_response):
        result = FetchResult(url="http://example.com", response=mock_response)
        assert result.content == b"hello"

    def test_content_type(self, mock_response):
        result = FetchResult(url="http://example.com", response=mock_response)
        assert result.content_type == "text/plain"


# ---------------------------------------------------------------------------
# FetchResult with error
# ---------------------------------------------------------------------------


class TestFetchResultWithError:
    def test_ok_is_false_when_error_set(self):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = 200
        result = FetchResult(url="http://example.com", response=resp, error="timeout")
        assert result.ok is False


# ---------------------------------------------------------------------------
# FetchResult.write_file
# ---------------------------------------------------------------------------


class TestFetchResultWriteFile:
    def test_write_file_creates_file(self, tmp_path):
        resp = MagicMock(spec=requests.Response)
        resp.content = b"file-content"
        result = FetchResult(url="http://example.com", response=resp)

        out = tmp_path / "output.txt"
        result.write_file(str(out))
        assert out.read_bytes() == b"file-content"

    def test_write_file_creates_parent_dirs(self, tmp_path):
        resp = MagicMock(spec=requests.Response)
        resp.content = b"nested"
        result = FetchResult(url="http://example.com", response=resp)

        out = tmp_path / "sub" / "dir" / "output.txt"
        result.write_file(str(out))
        assert out.read_bytes() == b"nested"


# ---------------------------------------------------------------------------
# fetch() function
# ---------------------------------------------------------------------------


class TestFetch:
    @patch("kayak.utils.http_client.requests.get")
    def test_fetch_success(self, mock_get):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_get.return_value = mock_resp

        result = fetch("http://example.com/data")
        assert result.ok is True
        assert result.url == "http://example.com/data"

    @patch("kayak.utils.http_client.requests.get")
    def test_fetch_passes_verify_false(self, mock_get):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        fetch("http://example.com/data")
        _, kwargs = mock_get.call_args
        assert kwargs["verify"] is False

    @patch("kayak.utils.http_client.requests.get")
    def test_fetch_passes_user_agent(self, mock_get):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        fetch("http://example.com/data")
        _, kwargs = mock_get.call_args
        assert "User-Agent" in kwargs["headers"]

    @patch("kayak.utils.http_client.time.sleep")
    @patch("kayak.utils.http_client.requests.get")
    def test_fetch_exception_returns_error_result(self, mock_get, mock_sleep):
        mock_get.side_effect = requests.ConnectionError("refused")
        result = fetch("http://example.com/data")
        assert result.ok is False
        assert result.error is not None

    @patch("kayak.utils.http_client.requests.get")
    def test_fetch_custom_timeout(self, mock_get):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp
        fetch("http://example.com/data", timeout=10)
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 10
