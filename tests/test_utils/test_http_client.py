"""Tests for kayak.utils.http_client."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import requests

from kayak.utils.http_client import FetchResult, _validate_url, async_fetch_many, fetch


@pytest.fixture(autouse=True)
def _public_getaddrinfo():
    """Keep _validate_url offline: return a fixed public IP (8.8.8.8) for any
    hostname. Tests that exercise the validator directly override this with
    their own `getaddrinfo` patch inside the test body."""
    with patch(
        "kayak.utils.http_client.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", ("8.8.8.8", 0))],
    ):
        yield


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
    @pytest.fixture(autouse=True)
    def mock_session(self):
        """Patch the module-level Session factory with a MagicMock.

        Tests assert against ``mock_session.get`` instead of patching
        ``requests.get`` directly. This matches the production pattern
        where every fetch goes through one pooled Session.
        """
        sess = MagicMock()
        with patch("kayak.utils.http_client._get_session", return_value=sess):
            yield sess

    def test_fetch_success(self, mock_session):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_session.get.return_value = mock_resp

        result = fetch("http://example.com/data")
        assert result.ok is True
        assert result.url == "http://example.com/data"

    def test_fetch_verifies_tls_by_default(self, mock_session):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp
        fetch("https://example.com/data")
        _, kwargs = mock_session.get.call_args
        assert kwargs["verify"] is True

    def test_fetch_skips_verify_for_insecure_host(self, mock_session):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp
        fetch("https://www.nwd-wc.usace.army.mil/foo")
        _, kwargs = mock_session.get.call_args
        assert kwargs["verify"] is False

    def test_fetch_passes_user_agent(self, mock_session):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp
        fetch("http://example.com/data")
        _, kwargs = mock_session.get.call_args
        assert "User-Agent" in kwargs["headers"]

    @patch("kayak.utils.http_client.time.sleep")
    def test_fetch_exception_returns_error_result(self, mock_sleep, mock_session):
        mock_session.get.side_effect = requests.ConnectionError("refused")
        result = fetch("http://example.com/data")
        assert result.ok is False
        assert result.error is not None

    def test_fetch_custom_timeout(self, mock_session):
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp
        fetch("http://example.com/data", timeout=10)
        _, kwargs = mock_session.get.call_args
        assert kwargs["timeout"] == 10

    def test_fetch_disables_redirects(self, mock_session):
        """Sync fetch must not follow redirects, else a 3xx could bypass
        _validate_url (the redirect target wouldn't be re-validated)."""
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        mock_session.get.return_value = mock_resp
        fetch("http://example.com/data")
        _, kwargs = mock_session.get.call_args
        assert kwargs["allow_redirects"] is False

    def test_fetch_rejects_ssrf_url(self, mock_session):
        """fetch() returns an error FetchResult (no HTTP call) when the URL
        resolves to an internal IP."""
        with patch(
            "kayak.utils.http_client.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("169.254.169.254", 0))],
        ):
            result = fetch("http://metadata.example/")
        assert result.ok is False
        assert result.error is not None
        assert "blocked IP" in result.error
        mock_session.get.assert_not_called()


class TestSessionPooling:
    """Two fetches go through the same pooled Session (one TLS handshake)."""

    def teardown_method(self) -> None:
        from kayak.utils.http_client import reset_session

        reset_session()

    def test_get_session_is_singleton(self):
        from kayak.utils.http_client import _get_session

        s1 = _get_session()
        s2 = _get_session()
        assert s1 is s2

    def test_reset_session_creates_a_new_one(self):
        from kayak.utils.http_client import _get_session, reset_session

        s1 = _get_session()
        reset_session()
        s2 = _get_session()
        assert s1 is not s2

    def test_user_agent_set_on_session(self):
        from kayak.config import FETCH_USER_AGENT
        from kayak.utils.http_client import _get_session

        sess = _get_session()
        assert sess.headers.get("User-Agent") == FETCH_USER_AGENT

    def test_two_fetches_share_session(self):
        """Both calls route through the same Session instance."""
        sess_mock = MagicMock()
        mock_resp = MagicMock(spec=requests.Response)
        mock_resp.status_code = 200
        sess_mock.get.return_value = mock_resp
        with patch("kayak.utils.http_client._get_session", return_value=sess_mock):
            fetch("http://example.com/a")
            fetch("http://example.com/b")
        assert sess_mock.get.call_count == 2


# ---------------------------------------------------------------------------
# _validate_url
# ---------------------------------------------------------------------------


class TestValidateUrl:
    def test_rejects_non_http_scheme(self):
        with pytest.raises(ValueError, match="Scheme not allowed"):
            _validate_url("file:///etc/passwd")
        with pytest.raises(ValueError, match="Scheme not allowed"):
            _validate_url("ftp://example.com/")

    def test_rejects_empty_hostname(self):
        with pytest.raises(ValueError, match="No hostname"):
            _validate_url("http:///path")

    def test_rejects_rfc1918(self):
        with (
            patch(
                "kayak.utils.http_client.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("10.0.0.1", 0))],
            ),
            pytest.raises(ValueError, match="blocked IP"),
        ):
            _validate_url("http://internal.example/")

    def test_rejects_loopback(self):
        with (
            patch(
                "kayak.utils.http_client.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
            ),
            pytest.raises(ValueError, match="blocked IP"),
        ):
            _validate_url("http://localhost/")

    def test_rejects_metadata_ip(self):
        with (
            patch(
                "kayak.utils.http_client.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", ("169.254.169.254", 0))],
            ),
            pytest.raises(ValueError, match="blocked IP"),
        ):
            _validate_url("http://metadata.cloud/")

    def test_accepts_public_ip(self):
        with patch(
            "kayak.utils.http_client.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("8.8.8.8", 0))],
        ):
            _validate_url("http://dns.example/")  # no raise

    def test_dns_failure_raises(self):
        import socket

        with (
            patch(
                "kayak.utils.http_client.socket.getaddrinfo",
                side_effect=socket.gaierror("nodename nor servname provided"),
            ),
            pytest.raises(ValueError, match="DNS resolution failed"),
        ):
            _validate_url("http://no-such-host.example/")


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

    def get(self, url, timeout=None, ssl=None):
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

        fake_session = _FakeSession(
            {
                "http://host-a.com/data1": _FakeAsyncResponse(200, "body1"),
                "http://host-a.com/data2": _FakeAsyncResponse(200, "body2"),
                "http://host-b.com/data3": _FakeAsyncResponse(200, "body3"),
            }
        )

        with (
            patch("kayak.utils.http_client.aiohttp.TCPConnector"),
            patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=fake_session),
        ):
            results = asyncio.run(async_fetch_many(urls, timeout=10))

        assert len(results) == 3
        for url in urls:
            assert results[url].ok is True
        assert results["http://host-a.com/data1"].text == "body1"
        assert results["http://host-b.com/data3"].text == "body3"

    def test_error_result(self):
        """async_fetch_many returns error FetchResult on connection failure."""
        urls = ["http://fail.com/bad"]

        fake_session = _FakeSession(
            {
                "http://fail.com/bad": aiohttp.ClientError("refused"),
            }
        )

        with (
            patch("kayak.utils.http_client.aiohttp.TCPConnector"),
            patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=fake_session),
            patch("kayak.utils.http_client.asyncio.sleep", new_callable=AsyncMock),
        ):
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

        with (
            patch("kayak.utils.http_client.aiohttp.TCPConnector"),
            patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=fake_session),
            patch("kayak.utils.http_client.asyncio.Semaphore", tracking_semaphore),
        ):
            results = asyncio.run(async_fetch_many(urls, concurrency_per_host=4, timeout=10))

        assert len(results) == 3
        # Two distinct hosts → two semaphores created
        assert len(semaphores_created) == 2

    def test_host_concurrency_override(self):
        """Hosts in the loaded overrides get their override limit; other
        hosts in the same batch keep concurrency_per_host."""
        urls = [
            "http://override.example/1",
            "http://normal.example/1",
        ]

        sem_limits: list[int] = []
        original_semaphore = asyncio.Semaphore

        def tracking_semaphore(n):
            sem_limits.append(n)
            return original_semaphore(n)

        fake_session = _FakeSession()

        with (
            patch(
                "kayak.utils.http_client._host_concurrency_overrides",
                return_value={"override.example": 2},
            ),
            patch("kayak.utils.http_client.aiohttp.TCPConnector"),
            patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=fake_session),
            patch("kayak.utils.http_client.asyncio.Semaphore", tracking_semaphore),
        ):
            results = asyncio.run(async_fetch_many(urls, concurrency_per_host=8, timeout=10))

        assert len(results) == 2
        assert sorted(sem_limits) == [2, 8]

    def test_budget_cancels_slow_urls(self):
        """A slow URL is cancelled when the batch budget runs out, but the
        fast URL still returns a real result and the batch as a whole exits
        promptly instead of hanging on the slow one."""
        urls = ["http://fast.com/ok", "http://slow.com/hang"]

        class _SlowResponse(_FakeAsyncResponse):
            async def __aenter__(self):
                # Sleep longer than the budget — simulates a hung connect or
                # a server that never replies.
                await asyncio.sleep(5)
                return self

        slow_session = _FakeSession(
            {
                "http://fast.com/ok": _FakeAsyncResponse(200, "fast-body"),
                "http://slow.com/hang": _SlowResponse(200, "would-be-slow"),
            }
        )

        with (
            patch("kayak.utils.http_client.aiohttp.TCPConnector"),
            patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=slow_session),
        ):
            results = asyncio.run(async_fetch_many(urls, timeout=10, budget=1))

        assert results["http://fast.com/ok"].ok is True
        assert results["http://fast.com/ok"].text == "fast-body"
        assert results["http://slow.com/hang"].ok is False
        assert results["http://slow.com/hang"].error == "batch budget exceeded"

    def test_budget_zero_disables(self):
        """budget=0 / None means no wall-clock cap — slow URLs still finish."""
        urls = ["http://normal.com/ok"]

        fake_session = _FakeSession({"http://normal.com/ok": _FakeAsyncResponse(200, "body")})

        with (
            patch("kayak.utils.http_client.aiohttp.TCPConnector"),
            patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=fake_session),
        ):
            results = asyncio.run(async_fetch_many(urls, timeout=10, budget=0))

        assert results["http://normal.com/ok"].ok is True

    def test_retry_on_503(self):
        """async_fetch_many retries on 503 status codes."""
        urls = ["http://retry.com/page"]

        call_count = 0

        class RetrySession:
            def get(self, url, timeout=None, ssl=None):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    return _FakeAsyncResponse(503, "unavailable")
                return _FakeAsyncResponse(200, "recovered")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with (
            patch("kayak.utils.http_client.aiohttp.TCPConnector"),
            patch("kayak.utils.http_client.aiohttp.ClientSession", return_value=RetrySession()),
            patch("kayak.utils.http_client.asyncio.sleep", new_callable=AsyncMock),
        ):
            results = asyncio.run(async_fetch_many(urls, timeout=10))

        assert results["http://retry.com/page"].ok is True
        assert results["http://retry.com/page"].text == "recovered"
        assert call_count == 3
