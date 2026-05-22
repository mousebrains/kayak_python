"""Public IP-reputation blocklist lookup (FireHOL Level 1).

FireHOL publishes community-curated blocklists at
https://iplists.firehol.org/. The Level 1 list ("attacks" category)
aggregates the highest-confidence sources — DShield, Feodo Tracker,
Spamhaus DROP, fullbogons — and is designed for use as a firewall
drop-list with minimum false positives. ~4500 CIDRs, ~71 KB, refreshed
hourly upstream.

We use it as one input signal in the bot classifier: any IP found in
the list classifies as ``blocklisted`` (a sibling of ``bot`` /
``scanner``). The list also includes IETF bogons (0.0.0.0/8, 127/8,
RFC1918, etc.) which would never appear in valid traffic to our nginx
anyway, so they're harmless noise in the matcher.

Mirrors the shape of :mod:`kayak.analytics.privacy_relays`: lazy fetch
on first lookup, disk cache with TTL, sorted-interval bisect lookup,
fail-open on network error. Lookups are O(log n) which for n=4500 is
microseconds — fine for the ~5k IPs per render.
"""

from __future__ import annotations

import bisect
import datetime as dt
import ipaddress
import logging
import os
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_FIREHOL_URL = "https://iplists.firehol.org/files/firehol_level1.netset"
_FIREHOL_CACHE_PATH = Path(
    os.environ.get(
        "KAYAK_FIREHOL_CACHE",
        "/home/pat/kayak/var/monitors/firehol_level1.netset",
    )
)
_FIREHOL_TTL_S = 7 * 86400  # 7 days
_DOWNLOAD_TIMEOUT_S = 30
_FETCH_UA = "Mozilla/5.0 (compatible; kayak-status; +https://levels.wkcc.org)"

# Sorted (lo_int, hi_int) interval lists, plus parallel lists of just
# the lo_ints for bisect. Built once on first lookup.
_v4_ranges: list[tuple[int, int]] | None = None
_v6_ranges: list[tuple[int, int]] | None = None
_v4_los: list[int] = []
_v6_los: list[int] = []
_firehol_fetch_disabled: bool = False


def _try_fetch_firehol() -> str | None:
    """Download the FireHOL Level 1 netset. Returns the body or None."""
    try:
        req = urllib.request.Request(_FIREHOL_URL, headers={"User-Agent": _FETCH_UA})
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            raw: bytes = resp.read()
            return raw.decode("utf-8", errors="replace")
    except OSError as exc:
        logger.warning("FireHOL netset fetch failed: %s", exc)
        return None


def _read_disk_netset() -> tuple[str | None, float]:
    """Read the cached netset and its age in seconds, or (None, inf)."""
    if not _FIREHOL_CACHE_PATH.exists():
        return None, float("inf")
    try:
        age = dt.datetime.now().timestamp() - _FIREHOL_CACHE_PATH.stat().st_mtime
        return _FIREHOL_CACHE_PATH.read_text(encoding="utf-8"), age
    except OSError:
        return None, float("inf")


def _write_disk_netset(body: str) -> None:
    try:
        _FIREHOL_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FIREHOL_CACHE_PATH.with_suffix(_FIREHOL_CACHE_PATH.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, _FIREHOL_CACHE_PATH)
    except OSError:
        pass


def _parse_netset_to_ranges(
    body: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Parse a FireHOL .netset into sorted (lo, hi) interval lists per family.

    The format is one CIDR (or bare IP) per line, with ``#``-prefixed
    comments and blank lines to ignore. Lines that fail to parse as a
    network are skipped.
    """
    v4: list[tuple[int, int]] = []
    v6: list[tuple[int, int]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            net = ipaddress.ip_network(line, strict=False)
        except ValueError:
            continue
        entry = (int(net.network_address), int(net.broadcast_address))
        (v4 if net.version == 4 else v6).append(entry)
    v4.sort()
    v6.sort()
    return v4, v6


def _ensure_loaded() -> None:
    """Hydrate the in-memory range tables from cache, refreshing if stale."""
    global _v4_ranges, _v6_ranges, _v4_los, _v6_los, _firehol_fetch_disabled
    if _v4_ranges is not None:
        return

    body, age = _read_disk_netset()
    needs_refresh = body is None or age >= _FIREHOL_TTL_S
    if needs_refresh and not _firehol_fetch_disabled:
        fresh = _try_fetch_firehol()
        if fresh is not None:
            _write_disk_netset(fresh)
            body = fresh
        elif body is None:
            # No disk fallback and the network failed — disable for the rest
            # of this process to avoid hammering the endpoint on every lookup.
            _firehol_fetch_disabled = True

    if body is None:
        _v4_ranges = []
        _v6_ranges = []
        _v4_los = []
        _v6_los = []
        return

    v4, v6 = _parse_netset_to_ranges(body)
    _v4_ranges = v4
    _v6_ranges = v6
    _v4_los = [r[0] for r in v4]
    _v6_los = [r[0] for r in v6]


def is_firehol_blocked(ip: str) -> bool:
    """``True`` if *ip* is in the cached FireHOL Level 1 blocklist."""
    _ensure_loaded()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    ip_int = int(addr)
    if addr.version == 4:
        ranges, los = _v4_ranges or [], _v4_los
    else:
        ranges, los = _v6_ranges or [], _v6_los
    if not ranges:
        return False
    idx = bisect.bisect_right(los, ip_int) - 1
    if idx < 0:
        return False
    lo, hi = ranges[idx]
    return lo <= ip_int <= hi


def reset_cache_for_tests() -> None:
    """Test helper: drop the parsed range cache + re-enable the fetcher."""
    global _v4_ranges, _v6_ranges, _v4_los, _v6_los, _firehol_fetch_disabled
    _v4_ranges = None
    _v6_ranges = None
    _v4_los = []
    _v6_los = []
    _firehol_fetch_disabled = False
