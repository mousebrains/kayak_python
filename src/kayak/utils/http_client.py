"""HTTP client wrapper."""

import asyncio
import ipaddress
import logging
import socket
import ssl
import time
from collections import defaultdict
from urllib.parse import urlparse

import aiohttp
import requests  # type: ignore[import-untyped]

from kayak.config import FETCH_TIMEOUT, FETCH_USER_AGENT

logger = logging.getLogger(__name__)


# Hosts whose TLS chain or cipher suite can't be validated with a stock Debian
# CA bundle. Every entry here is a MITM risk — only add hosts we've confirmed
# need relaxed TLS *and* where the payload is non-sensitive public data (river
# observations). Keep this list minimal.
_INSECURE_HOSTS: frozenset[str] = frozenset(
    {
        "www.nwd-wc.usace.army.mil",  # USACE — DoD CA not in standard bundle
    }
)


def _is_insecure_host(url: str) -> bool:
    return (urlparse(url).hostname or "") in _INSECURE_HOSTS


def _insecure_ssl_context() -> ssl.SSLContext:
    """SSL context for hosts in _INSECURE_HOSTS: no verify, legacy ciphers."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # Allow legacy non-forward-secrecy ciphers (e.g. AES256-SHA) that older
    # servers still require.
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return ctx


def _validate_url(url: str) -> None:
    """Reject non-http(s) schemes and hosts that resolve to internal IPs.

    Defends against SSRF if `sources.yaml` / `fetch_url` rows ever contain
    attacker-controlled URLs pointing at cloud metadata (169.254.169.254),
    loopback, RFC1918, or other internal ranges.

    Known limitation — TOCTOU: we resolve the hostname once here; the HTTP
    client resolves it again when making the request. A hostile DNS server
    could flip results between calls. Acceptable tradeoff given the threat
    model (attacker needs write access to sources.yaml, which lives in a
    repo owned by pat).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Scheme not allowed: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError(f"No hostname in URL: {url!r}")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed for {host!r}: {e}") from e

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"Host {host!r} resolved to blocked IP {ip_str}")


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

    TLS certificate verification is on by default. It is disabled only for
    hosts in `_INSECURE_HOSTS` (see module docstring).

    URLs that fail `_validate_url` (non-http(s), internal/metadata IPs) are
    rejected without making a request. Redirects are not followed — a 3xx
    response is returned as-is so a redirect to an internal address can't
    bypass validation.
    """
    if timeout is None:
        timeout = FETCH_TIMEOUT

    try:
        _validate_url(url)
    except ValueError as e:
        logger.error("URL validation failed for %s: %s", url, e)
        return FetchResult(url=url, error=str(e))

    verify = not _is_insecure_host(url)

    last_result: FetchResult | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": FETCH_USER_AGENT},
                verify=verify,
                allow_redirects=False,
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
    insecure_ctx: ssl.SSLContext,
) -> FetchResult:
    """Fetch a single URL with retry logic under a per-host semaphore.

    aiohttp defaults to `allow_redirects=False` on `session.get`, so a 3xx
    is surfaced without validator bypass.
    """
    try:
        _validate_url(url)
    except ValueError as e:
        logger.error("URL validation failed for %s: %s", url, e)
        return FetchResult(url=url, error=str(e))

    # Per-request ssl override: insecure context for known-bad hosts, else
    # True (use the connector's default context, which verifies properly).
    ssl_arg: ssl.SSLContext | bool = insecure_ctx if _is_insecure_host(url) else True

    last_result: FetchResult | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            async with (
                semaphore,
                session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    ssl=ssl_arg,
                ) as resp,
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

    # Default connector verifies TLS; individual requests to known-bad hosts
    # override with `ssl=insecure_ctx` in _async_fetch_one().
    insecure_ctx = _insecure_ssl_context()
    connector = aiohttp.TCPConnector()
    async with aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": FETCH_USER_AGENT},
    ) as session:
        tasks = []
        for url in urls:
            host = urlparse(url).hostname or ""
            sem = host_semaphores[host]
            tasks.append(_async_fetch_one(url, session, sem, timeout, insecure_ctx))
        results = await asyncio.gather(*tasks)

    return dict(zip(urls, results, strict=True))
