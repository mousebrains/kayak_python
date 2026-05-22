"""GeoIP lookups backed by DB-IP City Lite + DB-IP ASN Lite.

DB-IP publishes free city-level and ASN-level mmdbs monthly with no
registration required (https://db-ip.com/db/lite.php — CC-BY 4.0). The
City variant is ~130 MB decompressed and supplies, per IP, the country
(code + English name), first-level subdivision (US state / CA province),
city, continent, and approximate lat/lon. The ASN variant is ~6 MB
decompressed and supplies the autonomous system number + the
organization name (Alibaba Cloud, Hetzner, Comcast, etc.). Both ship
in the ``maxminddb`` format and are read through the same library.

Functions:
- :func:`lookup` — ISO 3166-1 alpha-2 country code.
- :func:`lookup_name` — English country name.
- :func:`lookup_subdivision` — English first-level subdivision name.
- :func:`lookup_asn` — autonomous system number (``int``; 0 if unknown).
- :func:`lookup_asn_org` — autonomous system organization name (``str``).

Behaviour:
- Lazily downloads the current month's DBs to ``/home/pat/kayak/var/geoip/``
  the first time any lookup needs them.
- Caches all five fields per-process (and persists to disk with 60-day
  TTL) so subsequent renders only touch the mmdbs for newly-seen IPs.
- Falls back to ``"-"`` / ``""`` / ``0`` for any IP that can't be
  classified (private, reserved, lookup error, DB missing).
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
_CITY_UPSTREAM = "https://download.db-ip.com/free/dbip-city-lite-{ym}.mmdb.gz"
_CITY_NAME_GLOB = "dbip-city-lite-*.mmdb"
_ASN_UPSTREAM = "https://download.db-ip.com/free/dbip-asn-lite-{ym}.mmdb.gz"
_ASN_NAME_GLOB = "dbip-asn-lite-*.mmdb"
_DOWNLOAD_TIMEOUT_S = 120  # City Lite is ~60 MB compressed; allow time on slow links
_DOWNLOAD_UA = "Mozilla/5.0 (compatible; kayak-status; +https://levels.wkcc.org)"

_city_reader: maxminddb.Reader | None = None
_city_reader_path: Path | None = None
_city_download_disabled: bool = False
_asn_reader: maxminddb.Reader | None = None
_asn_reader_path: Path | None = None
_asn_download_disabled: bool = False

# Per-process lookup cache. On-disk form: {ip: [code, name, sub, asn, asn_org, ts]}.
# In-memory split into per-field dicts so each lookup_* call is a single
# dict read.
_country_code: dict[str, str] = {}
_country_name: dict[str, str] = {}
_subdivision: dict[str, str] = {}
_asn_number: dict[str, int] = {}
_asn_org: dict[str, str] = {}
_lookup_last_seen: dict[str, int] = {}
_LOOKUP_CACHE_PATH = Path(
    os.environ.get("KAYAK_GEOIP_CACHE", "/home/pat/kayak/var/geoip/lookup_cache.json")
)
_LOOKUP_CACHE_TTL_DAYS = 60
_lookup_cache_loaded: bool = False


def _load_6tuple(ip: str, value: list, cutoff: int) -> None:
    c, n, s, a, ao, t = value
    if not (
        isinstance(c, str)
        and isinstance(n, str)
        and isinstance(s, str)
        and isinstance(a, int)
        and isinstance(ao, str)
        and isinstance(t, int)
    ):
        return
    if t < cutoff:
        return
    _country_code.setdefault(ip, c)
    _country_name.setdefault(ip, n)
    _subdivision.setdefault(ip, s)
    _asn_number.setdefault(ip, a)
    _asn_org.setdefault(ip, ao)
    _lookup_last_seen.setdefault(ip, t)


def _load_4tuple(ip: str, value: list, cutoff: int) -> None:
    """Pre-ASN format: country / name / subdivision / ts. Country fields load
    from the cache; ASN gets populated on first lookup_asn() call."""
    c, n, s, t = value
    if not (
        isinstance(c, str) and isinstance(n, str) and isinstance(s, str) and isinstance(t, int)
    ):
        return
    if t < cutoff:
        return
    _country_code.setdefault(ip, c)
    _country_name.setdefault(ip, n)
    _subdivision.setdefault(ip, s)
    _lookup_last_seen.setdefault(ip, t)


def _load_lookup_cache() -> None:
    """Load `{ip: [code, name, sub, asn, asn_org, ts]}` entries from disk.

    Tolerates the older 4-tuple shape `[code, name, sub, ts]` written by
    pre-ASN versions of this module — those entries load the country
    fields and leave the ASN fields unpopulated, so the next lookup_asn()
    call will fill them in. Older 2-tuple `[code, ts]` entries get
    discarded entirely.
    """
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
        if not isinstance(ip, str) or not isinstance(value, list):
            continue
        if len(value) == 6:
            _load_6tuple(ip, value, cutoff)
        elif len(value) == 4:
            _load_4tuple(ip, value, cutoff)


def _save_lookup_cache() -> None:
    try:
        _LOOKUP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = {
            ip: [
                _country_code.get(ip, ""),
                _country_name.get(ip, ""),
                _subdivision.get(ip, ""),
                _asn_number.get(ip, 0),
                _asn_org.get(ip, ""),
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


def _download_mmdb(db_dir: Path, *, upstream: str, name_prefix: str, ym: str | None = None) -> Path:
    """Download an mmdb of the given flavour for the current month.

    Tries the current month first, then the previous month — DB-IP
    releases on the 1st but the URL only becomes live after a delay,
    and at month-start ``today`` may already be in the next month
    before that month's file is published.
    """
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
        url = upstream.format(ym=month)
        target = db_dir / f"{name_prefix}{month}.mmdb"
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
    raise RuntimeError(
        f"could not download any DB-IP mmdb under {name_prefix} (last error: {last_err})"
    )


def _open_city_reader(db_dir: Path) -> maxminddb.Reader | None:
    global _city_reader, _city_reader_path, _city_download_disabled
    if _city_reader is not None:
        return _city_reader
    if _city_download_disabled:
        return None
    target = db_dir / f"dbip-city-lite-{dt.date.today():%Y-%m}.mmdb"
    existing = sorted(db_dir.glob(_CITY_NAME_GLOB))
    if not target.exists() and existing:
        target = existing[-1]
    if not target.exists():
        try:
            target = _download_mmdb(db_dir, upstream=_CITY_UPSTREAM, name_prefix="dbip-city-lite-")
        except Exception as exc:
            logger.warning("geoip city lookup disabled: %s", exc)
            _city_download_disabled = True
            return None
    try:
        _city_reader = maxminddb.open_database(str(target))
        _city_reader_path = target
        return _city_reader
    except Exception as exc:
        logger.warning("could not open city mmdb %s: %s", target, exc)
        _city_download_disabled = True
        return None


def _open_asn_reader(db_dir: Path) -> maxminddb.Reader | None:
    global _asn_reader, _asn_reader_path, _asn_download_disabled
    if _asn_reader is not None:
        return _asn_reader
    if _asn_download_disabled:
        return None
    target = db_dir / f"dbip-asn-lite-{dt.date.today():%Y-%m}.mmdb"
    existing = sorted(db_dir.glob(_ASN_NAME_GLOB))
    if not target.exists() and existing:
        target = existing[-1]
    if not target.exists():
        try:
            target = _download_mmdb(db_dir, upstream=_ASN_UPSTREAM, name_prefix="dbip-asn-lite-")
        except Exception as exc:
            logger.warning("geoip asn lookup disabled: %s", exc)
            _asn_download_disabled = True
            return None
    try:
        _asn_reader = maxminddb.open_database(str(target))
        _asn_reader_path = target
        return _asn_reader
    except Exception as exc:
        logger.warning("could not open asn mmdb %s: %s", target, exc)
        _asn_download_disabled = True
        return None


def _populate_country(ip: str, *, db_dir: Path) -> None:
    """Populate the country / name / subdivision fields for *ip*."""
    reader = _open_city_reader(db_dir)
    code = "-"
    name = ""
    sub = ""
    if reader is not None:
        try:
            record: dict[str, Any] | None = reader.get(ip)  # type: ignore[assignment]
        except (ValueError, maxminddb.InvalidDatabaseError):
            record = None
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


def _populate_asn(ip: str, *, db_dir: Path) -> None:
    """Populate the autonomous-system fields for *ip*."""
    reader = _open_asn_reader(db_dir)
    asn = 0
    org = ""
    if reader is not None:
        try:
            record: dict[str, Any] | None = reader.get(ip)  # type: ignore[assignment]
        except (ValueError, maxminddb.InvalidDatabaseError):
            record = None
        if isinstance(record, dict):
            n = record.get("autonomous_system_number")
            if isinstance(n, int):
                asn = n
            o = record.get("autonomous_system_organization")
            if isinstance(o, str):
                org = o
    _asn_number[ip] = asn
    _asn_org[ip] = org


def _ensure_country(ip: str, db_dir: Path) -> None:
    _load_lookup_cache()
    _lookup_last_seen[ip] = int(dt.datetime.now(dt.UTC).timestamp())
    if ip not in _country_code:
        _populate_country(ip, db_dir=db_dir)


def _ensure_asn(ip: str, db_dir: Path) -> None:
    _load_lookup_cache()
    _lookup_last_seen[ip] = int(dt.datetime.now(dt.UTC).timestamp())
    if ip not in _asn_number:
        _populate_asn(ip, db_dir=db_dir)


def lookup(ip: str, *, db_dir: Path = DEFAULT_DB_DIR) -> str:
    """ISO 3166-1 alpha-2 country code for *ip*, or ``"-"`` if unknown."""
    _ensure_country(ip, db_dir)
    return _country_code.get(ip, "-")


def lookup_name(ip: str, *, db_dir: Path = DEFAULT_DB_DIR) -> str:
    """English country name for *ip*, or ``""`` if unknown."""
    _ensure_country(ip, db_dir)
    return _country_name.get(ip, "")


def lookup_subdivision(ip: str, *, db_dir: Path = DEFAULT_DB_DIR) -> str:
    """English first-level subdivision (US state / CA province / …) for *ip*.

    Empty string for IPs the DB doesn't carry subdivision data for (most
    non-US/CA countries in the Lite variant).
    """
    _ensure_country(ip, db_dir)
    return _subdivision.get(ip, "")


def lookup_asn(ip: str, *, db_dir: Path = DEFAULT_DB_DIR) -> int:
    """Autonomous system number for *ip*, or ``0`` if unknown."""
    _ensure_asn(ip, db_dir)
    return _asn_number.get(ip, 0)


def lookup_asn_org(ip: str, *, db_dir: Path = DEFAULT_DB_DIR) -> str:
    """Autonomous system organization name for *ip*, or ``""`` if unknown."""
    _ensure_asn(ip, db_dir)
    return _asn_org.get(ip, "")


def flush_cache() -> None:
    """Write the geoip lookup cache to disk. Safe to call multiple times."""
    _save_lookup_cache()


def reset_cache_for_tests() -> None:
    """Test helper: drop the open readers + per-process cache."""
    global _city_reader, _city_reader_path, _city_download_disabled
    global _asn_reader, _asn_reader_path, _asn_download_disabled
    global _lookup_cache_loaded
    for reader in (_city_reader, _asn_reader):
        if reader is not None:
            reader.close()
    _city_reader = None
    _city_reader_path = None
    _city_download_disabled = False
    _asn_reader = None
    _asn_reader_path = None
    _asn_download_disabled = False
    _lookup_cache_loaded = False
    _country_code.clear()
    _country_name.clear()
    _subdivision.clear()
    _asn_number.clear()
    _asn_org.clear()
    _lookup_last_seen.clear()
