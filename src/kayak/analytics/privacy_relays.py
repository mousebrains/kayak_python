"""Identify privacy-relay egress IPs so the classifier doesn't false-positive them.

Apple iCloud Private Relay (iOS 15+ / macOS Monterey+ Safari, paid iCloud+)
proxies user traffic through two hops; the second hop egresses via Apple's
Fastly + Cloudflare contracts and Apple publishes the list of egress CIDRs
at ``https://mask-api.icloud.com/egress-ip-ranges.csv``. Each row is
``cidr,country,region,city,metadata`` — Apple deliberately preserves the
user's approximate country / region / city so the destination site can
still localize content. From our log's perspective Private Relay traffic
looks like:

- IP from one of Apple's listed CIDRs (Fastly or Cloudflare ASN per DB-IP)
- UA is normal Safari (iOS or macOS); never Applebot
- Hits often span multiple IPs because Private Relay rotates the egress
  per request — so a single browser session's HTML + CSS + JS each end
  up at different egress nodes, making individual IPs look incomplete
  (the no-assets classifier flagged ~11 % of relay IPs as bots in our
  24h sample before this module shipped)

This module fetches the CSV weekly, caches it on disk, builds a sorted-
interval lookup at module init, and exposes :func:`is_apple_private_relay`
+ :func:`apple_relay_region` for the classifier and per-IP table.

If we ever wanted to support other vendors, the shape would generalize:
Mullvad publishes their relay list at api.mullvad.net/www/relays/all/ and
Tor's exit list lives at check.torproject.org/torbulkexitlist. Microsoft
Edge Secure Network and Chrome IP Protection don't publish lists; their
traffic egresses through Cloudflare and is indistinguishable from generic
Cloudflare proxy traffic.
"""

from __future__ import annotations

import bisect
import csv
import datetime as dt
import ipaddress
import logging
import os
import urllib.request
from pathlib import Path

from kayak.config import STATUS_USER_AGENT

logger = logging.getLogger(__name__)

_APPLE_URL = "https://mask-api.icloud.com/egress-ip-ranges.csv"
_APPLE_CACHE_PATH = Path(
    os.environ.get(
        "KAYAK_APPLE_RELAY_CACHE",
        "/home/pat/kayak/var/monitors/icloud-private-relay.csv",
    )
)
_APPLE_TTL_S = 7 * 86400  # 7 days
_DOWNLOAD_TIMEOUT_S = 30
_FETCH_UA = STATUS_USER_AGENT

# Sorted (lo_int, hi_int, country, region, city) interval list, plus a
# parallel list of just the lo_ints for bisect. Built once on first lookup.
_v4_ranges: list[tuple[int, int, str, str, str]] | None = None
_v6_ranges: list[tuple[int, int, str, str, str]] | None = None
_v4_los: list[int] = []
_v6_los: list[int] = []
_apple_fetch_disabled: bool = False


def _try_fetch_apple_csv() -> str | None:
    """Download Apple's egress-IP CSV. Returns the body text or None."""
    try:
        req = urllib.request.Request(_APPLE_URL, headers={"User-Agent": _FETCH_UA})
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            raw: bytes = resp.read()
            return raw.decode("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("Apple Private Relay CSV fetch failed: %s", exc)
        return None


def _read_disk_csv() -> tuple[str | None, float]:
    """Read the cached CSV and its age in seconds, or (None, inf) if missing."""
    if not _APPLE_CACHE_PATH.exists():
        return None, float("inf")
    try:
        age = dt.datetime.now().timestamp() - _APPLE_CACHE_PATH.stat().st_mtime
        return _APPLE_CACHE_PATH.read_text(encoding="utf-8"), age
    except OSError:
        return None, float("inf")


def _write_disk_csv(body: str) -> None:
    try:
        _APPLE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _APPLE_CACHE_PATH.with_suffix(_APPLE_CACHE_PATH.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, _APPLE_CACHE_PATH)
    except OSError:
        pass


def _parse_csv_to_ranges(
    body: str,
) -> tuple[
    list[tuple[int, int, str, str, str]],
    list[tuple[int, int, str, str, str]],
]:
    """Parse the CSV into two sorted lists of (lo, hi, cc, region, city) tuples."""
    v4: list[tuple[int, int, str, str, str]] = []
    v6: list[tuple[int, int, str, str, str]] = []
    for row in csv.reader(body.splitlines()):
        if not row or not row[0]:
            continue
        cidr = row[0]
        cc = row[1] if len(row) > 1 else ""
        region = row[2] if len(row) > 2 else ""
        city = row[3] if len(row) > 3 else ""
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        entry = (int(net.network_address), int(net.broadcast_address), cc, region, city)
        (v4 if net.version == 4 else v6).append(entry)
    v4.sort()
    v6.sort()
    return v4, v6


def _ensure_loaded() -> None:
    """Load Apple's egress ranges into memory.

    Tries the on-disk cache first; falls back to a network refresh if the
    cache is missing or stale. Builds the sorted-interval lookup tables.
    """
    global _v4_ranges, _v6_ranges, _v4_los, _v6_los, _apple_fetch_disabled
    if _v4_ranges is not None:
        return

    body, age = _read_disk_csv()
    needs_refresh = body is None or age >= _APPLE_TTL_S
    if needs_refresh and not _apple_fetch_disabled:
        fresh = _try_fetch_apple_csv()
        if fresh is not None:
            _write_disk_csv(fresh)
            body = fresh
        elif body is None:
            # No disk fallback and the network failed — disable for the rest
            # of this process to avoid hammering the API on every lookup.
            _apple_fetch_disabled = True

    if body is None:
        _v4_ranges = []
        _v6_ranges = []
        _v4_los = []
        _v6_los = []
        return

    v4, v6 = _parse_csv_to_ranges(body)
    _v4_ranges = v4
    _v6_ranges = v6
    _v4_los = [r[0] for r in v4]
    _v6_los = [r[0] for r in v6]


def _find_range(ip: str) -> tuple[int, int, str, str, str] | None:
    """Return the (lo, hi, cc, region, city) tuple for the CIDR containing *ip*."""
    _ensure_loaded()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    ip_int = int(addr)
    if addr.version == 4:
        ranges, los = _v4_ranges or [], _v4_los
    else:
        ranges, los = _v6_ranges or [], _v6_los
    if not ranges:
        return None
    idx = bisect.bisect_right(los, ip_int) - 1
    if idx < 0:
        return None
    lo, hi, cc, region, city = ranges[idx]
    if lo <= ip_int <= hi:
        return (lo, hi, cc, region, city)
    return None


def is_apple_private_relay(ip: str) -> bool:
    """``True`` if *ip* is in Apple's published Private Relay egress ranges."""
    return _find_range(ip) is not None


def apple_relay_region(ip: str) -> tuple[str, str, str] | None:
    """Apple-reported (country, region, city) hint for *ip*, or ``None``.

    Apple's CSV preserves the user's approximate location even though the
    egress IP itself belongs to Fastly / Cloudflare. So a hit from a
    Fastly Bremerton egress on behalf of a Seattle user reports
    (``US``, ``US-WA``, ``BREMERTON``) — Apple chose Bremerton as the
    egress for that user's request.
    """
    found = _find_range(ip)
    if found is None:
        return None
    return (found[2], found[3], found[4])


def reset_cache_for_tests() -> None:
    """Test helper: drop the parsed range cache + re-enable fetcher."""
    global _v4_ranges, _v6_ranges, _v4_los, _v6_los, _apple_fetch_disabled
    _v4_ranges = None
    _v6_ranges = None
    _v4_los = []
    _v6_los = []
    _apple_fetch_disabled = False
