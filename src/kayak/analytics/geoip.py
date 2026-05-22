"""GeoIP lookups backed by DB-IP City Lite.

DB-IP publishes a free city-level mmdb monthly with no registration
required (https://db-ip.com/db/lite.php — CC-BY 4.0). The City variant
is ~130 MB decompressed and supplies, per IP, the country (code +
English name), first-level subdivision (US state / CA province), city,
continent, and approximate lat/lon — all via point-in-time lookups
through the ``maxminddb`` library.

Functions:
- :func:`lookup` returns the ISO 3166-1 alpha-2 country code for
  back-compat with run_humans' compact per-IP table.
- :func:`lookup_name` returns the English country name.
- :func:`lookup_subdivision` returns the English subdivision name
  (only useful for countries with administrative divisions in DB-IP
  — US, CA, AU, … — empty string otherwise).

Behaviour:
- Lazily downloads the current month's DB to ``/home/pat/kayak/var/geoip/``
  the first time any lookup is called.
- Caches results per-process (and persists to disk with 60-day TTL) so
  the same IP isn't queried twice in one render and so subsequent
  renders only touch the mmdb for newly-seen IPs.
- Falls back to ``"-"`` / ``""`` for any IP that can't be classified.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os
import urllib.request
from pathlib import Path
from typing import Any

import maxminddb

logger = logging.getLogger(__name__)

DEFAULT_DB_DIR = Path("/home/pat/kayak/var/geoip")
_UPSTREAM = "https://download.db-ip.com/free/dbip-city-lite-{ym}.mmdb.gz"
_DB_NAME_GLOB = "dbip-city-lite-*.mmdb"
_DOWNLOAD_TIMEOUT_S = 120  # City Lite is ~60 MB compressed; allow time on slow links
_DOWNLOAD_UA = "Mozilla/5.0 (compatible; kayak-status; +https://levels.wkcc.org)"

_reader: maxminddb.Reader | None = None
_reader_path: Path | None = None
_download_disabled: bool = False

# Per-process lookup cache. On-disk form: {ip: [code, name, subdivision, ts]}.
# In-memory we split that into three dicts plus a last-seen tracker so the
# read path doesn't have to unpack tuples for each call.
_country_code: dict[str, str] = {}
_country_name: dict[str, str] = {}
_subdivision: dict[str, str] = {}
_lookup_last_seen: dict[str, int] = {}
_LOOKUP_CACHE_PATH = Path(
    os.environ.get("KAYAK_GEOIP_CACHE", "/home/pat/kayak/var/geoip/lookup_cache.json")
)
_LOOKUP_CACHE_TTL_DAYS = 60
_lookup_cache_loaded: bool = False


def _load_lookup_cache() -> None:
    global _lookup_cache_loaded
    if _lookup_cache_loaded:
        return
    _lookup_cache_loaded = True
    try:
        with open(_LOOKUP_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    cutoff = now_ts - _LOOKUP_CACHE_TTL_DAYS * 86400
    for ip, value in data.items():
        if not isinstance(ip, str):
            continue
        # Current format: [code, name, subdivision, ts]. Discard anything else
        # (including the old 2-tuple [code, ts] from before the City-Lite
        # rewrite) — those entries will get re-populated against the City
        # Lite mmdb on next lookup, giving them name + subdivision data.
        if not (isinstance(value, list) and len(value) == 4):
            continue
        c, n, s, t = value
        if not (
            isinstance(c, str) and isinstance(n, str) and isinstance(s, str) and isinstance(t, int)
        ):
            continue
        if t < cutoff:
            continue
        _country_code.setdefault(ip, c)
        _country_name.setdefault(ip, n)
        _subdivision.setdefault(ip, s)
        _lookup_last_seen.setdefault(ip, t)


def _save_lookup_cache() -> None:
    try:
        _LOOKUP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = {
            ip: [
                _country_code.get(ip, ""),
                _country_name.get(ip, ""),
                _subdivision.get(ip, ""),
                _lookup_last_seen.get(ip, 0),
            ]
            for ip in _lookup_last_seen
        }
        tmp = _LOOKUP_CACHE_PATH.with_suffix(_LOOKUP_CACHE_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, _LOOKUP_CACHE_PATH)
    except OSError:
        pass


def _current_db_path(db_dir: Path) -> Path:
    return db_dir / f"dbip-city-lite-{dt.date.today():%Y-%m}.mmdb"


def _download_db(db_dir: Path, *, ym: str | None = None) -> Path:
    db_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today()
    months = (
        [ym]
        if ym is not None
        else [
            f"{today:%Y-%m}",
            f"{(today.replace(day=1) - dt.timedelta(days=1)):%Y-%m}",
        ]
    )
    last_err: Exception | None = None
    for month in months:
        url = _UPSTREAM.format(ym=month)
        target = db_dir / f"dbip-city-lite-{month}.mmdb"
        if target.exists():
            return target
        try:
            logger.info("downloading %s → %s", url, target)
            req = urllib.request.Request(url, headers={"User-Agent": _DOWNLOAD_UA})
            with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
                if resp.status != 200:
                    raise OSError(f"HTTP {resp.status} from {url}")
                blob = gzip.decompress(resp.read())
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(blob)
            tmp.replace(target)
            return target
        except Exception as exc:
            logger.warning("DB-IP download failed for %s: %s", month, exc)
            last_err = exc
    raise RuntimeError(f"could not download any DB-IP City Lite mmdb (last error: {last_err})")


def _open_reader(db_dir: Path) -> maxminddb.Reader | None:
    global _reader, _reader_path, _download_disabled
    if _reader is not None:
        return _reader
    if _download_disabled:
        return None
    target = _current_db_path(db_dir)
    # Reuse any older City Lite mmdb already on disk if the current month
    # isn't there yet.
    existing = sorted(db_dir.glob(_DB_NAME_GLOB))
    if not target.exists() and existing:
        target = existing[-1]
    if not target.exists():
        try:
            target = _download_db(db_dir)
        except Exception as exc:
            logger.warning("geoip lookup disabled: %s", exc)
            _download_disabled = True
            return None
    try:
        _reader = maxminddb.open_database(str(target))
        _reader_path = target
        return _reader
    except Exception as exc:
        logger.warning("could not open %s: %s", target, exc)
        _download_disabled = True
        return None


def _populate(ip: str, *, db_dir: Path) -> None:
    """Look up *ip* in the mmdb and cache code / name / subdivision."""
    reader = _open_reader(db_dir)
    if reader is None:
        _country_code[ip] = "-"
        _country_name[ip] = ""
        _subdivision[ip] = ""
        return
    try:
        record: dict[str, Any] | None = reader.get(ip)  # type: ignore[assignment]
    except (ValueError, maxminddb.InvalidDatabaseError):
        _country_code[ip] = "-"
        _country_name[ip] = ""
        _subdivision[ip] = ""
        return
    code = "-"
    name = ""
    sub = ""
    if isinstance(record, dict):
        country = record.get("country")
        if isinstance(country, dict):
            code = str(country.get("iso_code") or "-")
            names = country.get("names")
            if isinstance(names, dict):
                name = str(names.get("en") or "")
        subs = record.get("subdivisions")
        if isinstance(subs, list) and subs:
            first = subs[0]
            if isinstance(first, dict):
                snames = first.get("names")
                if isinstance(snames, dict):
                    sub = str(snames.get("en") or "")
    _country_code[ip] = code
    _country_name[ip] = name
    _subdivision[ip] = sub


def _ensure(ip: str, db_dir: Path) -> None:
    _load_lookup_cache()
    _lookup_last_seen[ip] = int(dt.datetime.now(dt.UTC).timestamp())
    if ip in _country_code:
        return
    _populate(ip, db_dir=db_dir)


def lookup(ip: str, *, db_dir: Path = DEFAULT_DB_DIR) -> str:
    """ISO 3166-1 alpha-2 country code for *ip*, or ``"-"`` if unknown."""
    _ensure(ip, db_dir)
    return _country_code.get(ip, "-")


def lookup_name(ip: str, *, db_dir: Path = DEFAULT_DB_DIR) -> str:
    """English country name for *ip*, or ``""`` if unknown."""
    _ensure(ip, db_dir)
    return _country_name.get(ip, "")


def lookup_subdivision(ip: str, *, db_dir: Path = DEFAULT_DB_DIR) -> str:
    """English first-level subdivision (US state / CA province / …) for *ip*.

    Empty string for IPs the DB doesn't carry subdivision data for (most
    non-US/CA countries in the Lite variant).
    """
    _ensure(ip, db_dir)
    return _subdivision.get(ip, "")


def flush_cache() -> None:
    """Write the geoip lookup cache to disk. Safe to call multiple times."""
    _save_lookup_cache()


def reset_cache_for_tests() -> None:
    """Test helper: drop the open reader + per-process cache."""
    global _reader, _reader_path, _download_disabled, _lookup_cache_loaded
    if _reader is not None:
        _reader.close()
    _reader = None
    _reader_path = None
    _download_disabled = False
    _lookup_cache_loaded = False
    _country_code.clear()
    _country_name.clear()
    _subdivision.clear()
    _lookup_last_seen.clear()
