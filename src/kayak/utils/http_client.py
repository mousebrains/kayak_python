"""HTTP client wrapper (replaces Curl.C/H).

Provides a simple requests-based HTTP client with the same interface
semantics as the C++ Curl class.
"""

from __future__ import annotations

import logging

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


def fetch(url: str, timeout: int | None = None) -> FetchResult:
    """Fetch a URL and return a FetchResult.

    Mirrors the C++ Curl constructor behavior:
    - SSL verification disabled (verify=False)
    - 5-minute default timeout
    - Custom user agent
    """
    if timeout is None:
        timeout = FETCH_TIMEOUT

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": FETCH_USER_AGENT},
            verify=False,
        )
        return FetchResult(url=url, response=response)
    except requests.RequestException as e:
        logger.error("Fetch error for %s: %s", url, e)
        return FetchResult(url=url, error=str(e))
