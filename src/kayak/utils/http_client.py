"""HTTP client wrapper."""

import asyncio
import logging
import ssl
import time
from collections import defaultdict
from urllib.parse import urlparse

import aiohttp
import requests  # type: ignore[import-untyped]

from kayak.config import FETCH_TIMEOUT, FETCH_USER_AGENT

logger = logging.getLogger(__name__)


class FetchResult:
    """Result of an HTTP fetch (mirrors Curl class interface)."""

    def __init__(
        self,
        url: str,
        response: requests.Response | None = None,
        error: str | None = None,
    ):
        self.url = url
        self._response = response
        self.error = error

    @property
    def ok(self) -> bool:
        """True if the request succeeded (mirrors Curl::operator bool)."""
        return self._response is not None and self.error is None

    @property
    def status_code(self) -> int:
        """HTTP status code (mirrors Curl::responseCode)."""
        if self._response is None:
            return 0
        return int(self._response.status_code)

    @property
    def content_type(self) -> str:
        """Content-Type header (mirrors Curl::contentType)."""
        if self._response is None:
            return ""
        return str(self._response.headers.get("Content-Type", ""))

    @property
    def text(self) -> str:
        """Response body as text (mirrors Curl::str)."""
        if self._response is None:
            return ""
        return str(self._response.text)

    @property
    def content(self) -> bytes:
        """Response body as bytes."""
        if self._response is None:
            return b""
        return bytes(self._response.content)

    def write_file(self, path: str) -> None:
        """Write response body to file (mirrors Curl::writeFile)."""
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self.content)


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3


def fetch(url: str, timeout: int | None = None) -> FetchResult:
    """Fetch a URL and return a FetchResult.

    Mirrors the C++ Curl constructor behavior:
    - SSL verification disabled (verify=False)
    - 5-minute default timeout
    - Custom user agent
    - Retries up to 3 times on transient errors with exponential backoff
    """
    if timeout is None:
        timeout = FETCH_TIMEOUT

    last_result: FetchResult | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": FETCH_USER_AGENT},
                verify=False,
            )
            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES - 1:
                wait = 2**attempt
                logger.warning(
                    "HTTP %d for %s, retrying in %ds (attempt %d/%d)",
                    response.status_code,
                    url,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                time.sleep(wait)
                last_result = FetchResult(url=url, response=response)
                continue
            return FetchResult(url=url, response=response)
        except requests.ConnectionError as e:
            if attempt < _MAX_RETRIES - 1:
                wait = 2**attempt
                logger.warning(
                    "Connection error for %s, retrying in %ds (attempt %d/%d): %s",
                    url,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                    e,
                )
                time.sleep(wait)
                last_result = FetchResult(url=url, error=str(e))
                continue
            logger.error("Fetch error for %s: %s", url, e)
            return FetchResult(url=url, error=str(e))
        except requests.RequestException as e:
            logger.error("Fetch error for %s: %s", url, e)
            return FetchResult(url=url, error=str(e))

    return last_result or FetchResult(url=url, error="Max retries exceeded")


async def _async_fetch_one(
    url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    timeout: int,
) -> FetchResult:
    """Fetch a single URL with retry logic under a per-host semaphore."""
    last_result: FetchResult | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            async with (
                semaphore,
                session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
            ):
                body = await resp.text(errors="replace")
                status = resp.status
                headers = dict(resp.headers)

            if status in _RETRYABLE_STATUS_CODES and attempt < _MAX_RETRIES - 1:
                wait = 2**attempt
                logger.warning(
                    "HTTP %d for %s, retrying in %ds (attempt %d/%d)",
                    status,
                    url,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(wait)
                # Build a lightweight mock response for FetchResult
                mock_resp = _SimpleResponse(status, body, headers)
                last_result = FetchResult(url=url, response=mock_resp)  # type: ignore[arg-type]
                continue
            mock_resp = _SimpleResponse(status, body, headers)
            return FetchResult(url=url, response=mock_resp)  # type: ignore[arg-type]
        except (aiohttp.ClientError, TimeoutError) as e:
            if attempt < _MAX_RETRIES - 1:
                wait = 2**attempt
                logger.warning(
                    "Connection error for %s, retrying in %ds (attempt %d/%d): %s",
                    url,
                    wait,
                    attempt + 1,
                    _MAX_RETRIES,
                    e,
                )
                await asyncio.sleep(wait)
                last_result = FetchResult(url=url, error=str(e))
                continue
            logger.error("Fetch error for %s: %s", url, e)
            return FetchResult(url=url, error=str(e))

    return last_result or FetchResult(url=url, error="Max retries exceeded")


class _SimpleResponse:
    """Minimal response object compatible with FetchResult's property access."""

    def __init__(self, status_code: int, text: str, headers: dict[str, str]):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8", errors="replace")
        self.headers = headers


async def async_fetch_many(
    urls: list[str],
    concurrency_per_host: int = 8,
    timeout: int | None = None,
) -> dict[str, FetchResult]:
    """Fetch multiple URLs concurrently with per-host concurrency limits.

    Groups URLs by hostname and creates one semaphore per host to avoid
    overwhelming any single server. Returns a dict mapping URL → FetchResult.
    """
    if timeout is None:
        timeout = FETCH_TIMEOUT

    # Group URLs by hostname and create per-host semaphores
    host_semaphores: dict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(concurrency_per_host)
    )

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    # Lower cipher security level to include non-forward-secrecy ciphers
    # (e.g. AES256-SHA) needed by older servers like USACE.
    ssl_ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": FETCH_USER_AGENT},
    ) as session:
        tasks = []
        for url in urls:
            host = urlparse(url).hostname or ""
            sem = host_semaphores[host]
            tasks.append(_async_fetch_one(url, session, sem, timeout))
        results = await asyncio.gather(*tasks)

    return dict(zip(urls, results, strict=True))
