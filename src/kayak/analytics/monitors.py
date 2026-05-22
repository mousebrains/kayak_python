"""Known external-monitor IP lists.

Better Stack (https://uptime.betterstack.com) publishes its monitor IPs at
``https://uptime.betterstack.com/ips-by-cluster.json`` — 34 bare IPv4 + IPv6
addresses grouped by region. We fetch and cache the file weekly so the
classifier in ``humans.py`` can label those hits as ``"monitor"`` instead
of mixing them in with attack scanners or random ``/``-hammer bots.

If the list can't be fetched (network down, host not reachable), the
classifier just falls back to its other heuristics — no false positives,
no hard fail.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BETTERSTACK_URL = "https://uptime.betterstack.com/ips-by-cluster.json"
_BETTERSTACK_CACHE_PATH = Path(
    os.environ.get("KAYAK_BETTERSTACK_CACHE", "/home/pat/kayak/var/monitors/betterstack.json")
)
_BETTERSTACK_TTL_S = 7 * 86400  # 7 days
_DOWNLOAD_TIMEOUT_S = 15
_FETCH_UA = "Mozilla/5.0 (compatible; kayak-status; +https://levels.wkcc.org)"

_betterstack_ips: set[str] | None = None
_betterstack_fetch_disabled: bool = False


def _try_fetch_betterstack() -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(_BETTERSTACK_URL, headers={"User-Agent": _FETCH_UA})
        with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Better Stack IP fetch failed: %s", exc)
        return None


def _read_disk_cache() -> tuple[dict[str, Any] | None, float]:
    """Return the on-disk cache and its age in seconds, or (None, inf) if missing."""
    if not _BETTERSTACK_CACHE_PATH.exists():
        return None, float("inf")
    try:
        age = dt.datetime.now().timestamp() - _BETTERSTACK_CACHE_PATH.stat().st_mtime
        with open(_BETTERSTACK_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data, age
    except (OSError, json.JSONDecodeError):
        return None, float("inf")


def _write_disk_cache(data: dict[str, Any]) -> None:
    try:
        _BETTERSTACK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _BETTERSTACK_CACHE_PATH.with_suffix(_BETTERSTACK_CACHE_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, _BETTERSTACK_CACHE_PATH)
    except OSError:
        pass


def _extract_ips(data: dict[str, Any] | None) -> set[str]:
    ips: set[str] = set()
    if not isinstance(data, dict):
        return ips
    for cluster_ips in data.values():
        if not isinstance(cluster_ips, list):
            continue
        for ip in cluster_ips:
            if isinstance(ip, str):
                ips.add(ip)
    return ips


def _load_or_refresh_betterstack() -> set[str]:
    """Return the cached Better Stack IP set, refreshing if older than TTL.

    Returns an empty set on persistent fetch failure so the caller's lookup
    is always defined.
    """
    global _betterstack_ips, _betterstack_fetch_disabled
    if _betterstack_ips is not None:
        return _betterstack_ips

    cached, cache_age = _read_disk_cache()
    needs_refresh = cached is None or cache_age >= _BETTERSTACK_TTL_S
    if needs_refresh and not _betterstack_fetch_disabled:
        fresh = _try_fetch_betterstack()
        if fresh is not None:
            _write_disk_cache(fresh)
            cached = fresh
        else:
            _betterstack_fetch_disabled = True

    _betterstack_ips = _extract_ips(cached)
    return _betterstack_ips


def is_betterstack(ip: str) -> bool:
    """``True`` if *ip* is in the published Better Stack monitor IP list."""
    return ip in _load_or_refresh_betterstack()


def reset_cache_for_tests() -> None:
    """Test helper: drop cached IP set + re-allow the fetcher."""
    global _betterstack_ips, _betterstack_fetch_disabled
    _betterstack_ips = None
    _betterstack_fetch_disabled = False
