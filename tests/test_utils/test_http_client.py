"""Tests for kayak.utils.http_client."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import requests

from kayak.utils.http_client import FetchResult, async_fetch_many, fetch

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


# ---------------------------------------------------------------------------
# async_fetch_many()
# ---------------------------------------------------------------------------


class _FakeAsyncResponse:
    """Minimal async context manager mimicking aiohttp response."""

    def __init__(self, status=200, body="ok", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "text/plain"}

    async def text(self, errors="replace"):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeSession:
    """Minimal async context manager mimicking aiohttp.ClientSession."""

    def __init__(self, responses=None):
        self._responses = responses or {}
        self._call_count = 0

    def get(self, url, timeout=None):
        if url in self._responses:
            resp = self._responses[url]
            if isinstance(resp, list):
                r = resp[self._call_count % len(resp)]
                self._call_count += 1
                if isinstance(r, Exception):
                    raise r
                return r
            if isinstance(resp, Exception):
                raise resp
            return resp
        return _FakeAsyncResponse()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class TestAsyncFetchMany:
    def test_success(self):
        """async_fetch_many returns results for all URLs."""
        urls = [
            "http://host-a.com/data1",
            "http://host-a.com/data2",
            "http://host-b.com/data3",
        ]

        fake_session = _FakeSession({
            "http://host-a.com/data1": _FakeAsyncResponse(200, "body1"),
            "http://host-a.com/data2": _FakeAsyncResponse(200, "body2"),
            "http://host-b.com/data3": _FakeAsyncResponse(200, "body3"),
        })

        with patch("kayak.utils.http_client.aiohttp.TCPConnector"), \
             patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=fake_session):
            results = asyncio.run(async_fetch_many(urls, timeout=10))

        assert len(results) == 3
        for url in urls:
            assert results[url].ok is True
        assert results["http://host-a.com/data1"].text == "body1"
        assert results["http://host-b.com/data3"].text == "body3"

    def test_error_result(self):
        """async_fetch_many returns error FetchResult on connection failure."""
        urls = ["http://fail.com/bad"]

        fake_session = _FakeSession({
            "http://fail.com/bad": aiohttp.ClientError("refused"),
        })

        with patch("kayak.utils.http_client.aiohttp.TCPConnector"), \
             patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=fake_session), \
             patch("kayak.utils.http_client.asyncio.sleep", new_callable=AsyncMock):
            results = asyncio.run(async_fetch_many(urls, timeout=10))

        assert len(results) == 1
        assert results["http://fail.com/bad"].ok is False
        assert results["http://fail.com/bad"].error is not None

    def test_per_host_grouping(self):
        """URLs to different hosts get independent semaphores."""
        urls = [
            "http://host-a.com/1",
            "http://host-a.com/2",
            "http://host-b.com/1",
        ]

        semaphores_created = []
        original_semaphore = asyncio.Semaphore

        def tracking_semaphore(n):
            sem = original_semaphore(n)
            semaphores_created.append(sem)
            return sem

        fake_session = _FakeSession()

        with patch("kayak.utils.http_client.aiohttp.TCPConnector"), \
             patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=fake_session), \
             patch("kayak.utils.http_client.asyncio.Semaphore", tracking_semaphore):
            results = asyncio.run(async_fetch_many(urls, concurrency_per_host=4, timeout=10))

        assert len(results) == 3
        # Two distinct hosts → two semaphores created
        assert len(semaphores_created) == 2

    def test_retry_on_503(self):
        """async_fetch_many retries on 503 status codes."""
        urls = ["http://retry.com/page"]

        call_count = 0

        class RetrySession:
            def get(self, url, timeout=None):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    return _FakeAsyncResponse(503, "unavailable")
                return _FakeAsyncResponse(200, "recovered")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("kayak.utils.http_client.aiohttp.TCPConnector"), \
             patch("kayak.utils.http_client.aiohttp.ClientSession",
                   return_value=RetrySession()), \
             patch("kayak.utils.http_client.asyncio.sleep", new_callable=AsyncMock):
            results = asyncio.run(async_fetch_many(urls, timeout=10))

        assert results["http://retry.com/page"].ok is True
        assert results["http://retry.com/page"].text == "recovered"
        assert call_count == 3
