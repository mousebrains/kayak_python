"""Human-vs-bot traffic analysis.

Two reports, sharing the bot/uptrends/scanner filter:

- ``run_humans()`` — distinct human visitors over a window, with per-IP
  detail. Ports ``~/logs.analyze/human_users.py``.
- ``run_chunked()`` — 2h-bucketed human/bot/other counts. Ports
  ``~/logs.analyze/chunked_humans.py``.

Both consume the ``AccessEvent`` iterator from ``_log_sources`` and
emit Markdown to stdout via the CLI wrapper.
"""

from __future__ import annotations

import collections
import concurrent.futures
import datetime as dt
import json
import os
import re
import socket
from collections.abc import Iterable
from pathlib import Path

from . import geoip, monitors
from ._log_sources import AccessEvent, iter_access_events

# ----------------------------------------------------------------------
# Filter helpers (verbatim from chunked_humans.py + human_users.py)
# ----------------------------------------------------------------------

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|preview|monitor|uptime|chatgpt|claude|gpt|perplexity|"
    r"applebot|baidu|yandex|petal|semrush|ahrefs|mj12|dotbot|seznam|duckduck|google|"
    r"sogou|exabot|coccoc|ia_archiver|mediapartners|telegram|whatsapp|skype|httpx|"
    r"python-requests|curl|wget|go-http|node-fetch|java/|okhttp|libwww|scrapy|"
    r"headlesschrome|domain-audit|micromessenger|scan|fetch/|axios|aiohttp|"
    r"censys|internetmeasurement|infrawatch|validator|sortsite|ct2ips",
    re.I,
)

UPTRENDS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
FAKE_MSIE7 = "Mozilla/4.0 (compatible; MSIE 7.0; Windows NT 6.0)"

SCANNER_ONLY_PATHS = frozenset(
    {
        "/.env",
        "/.git/config",
        "/.git/HEAD",
        "/wp-login.php",
        "/wp-admin",
        "/xmlrpc.php",
        "/robots.txt",
    }
)

# Asset extensions that ride along with normal page loads — not pages a
# user navigates to. Filtered out of the per-URL hits table so the operator
# sees what people are actually *looking at*, not the noise of every CSS /
# JS / image request that comes with each page.
_ASSET_EXTENSIONS = frozenset(
    {
        ".css",
        ".js",
        ".json",
        ".geojson",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".ico",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".map",
    }
)


def _is_asset_path(path: str) -> bool:
    p = path.lower().rstrip("/")
    dot = p.rfind(".")
    if dot < 0:
        return False
    return p[dot:] in _ASSET_EXTENSIONS


# In-memory rdns cache. The on-disk format keeps last-seen so stale entries
# can be evicted — but everything past that timestamp drops at load time, so
# the in-memory dict only ever holds {ip: name} (a known PTR record, or ""
# for confirmed-no-PTR / lookup-failed).
_rdns_cache: dict[str, str] = {}
# IPs touched in this process (logs OR fresh lookup). Used to refresh the
# last-seen stamp before saving so a steady-state IP doesn't expire.
_rdns_last_seen: dict[str, int] = {}
# IPs we've already taken a swing at this process. If a lookup timed out
# under one warm_rdns call's budget, subsequent calls in the same process
# don't waste budget retrying it — we just leave it uncached for this run
# and the next render can have another go.
_rdns_attempted: set[str] = set()

# socket.gethostbyaddr is a blocking C call that ignores socket.setdefaulttimeout;
# a single unreachable resolver can hang it indefinitely. Resolve the whole IP set
# in parallel with a wall-clock budget — anything past the deadline gets retried
# next run (uncached). Defaults sized for the nightly status render: ~4k IPs/day,
# of which ~60% have no PTR and stall ~10s before NXDOMAIN. The on-disk cache
# means only NEW IPs need a lookup on subsequent runs, so steady-state is fast.
_RDNS_WORKERS = 128
_RDNS_TOTAL_BUDGET_S = 180.0

# Persistent on-disk rdns cache: subsequent renders only have to look up the
# IPs that are new since the last run. Path is overridable via env so tests
# don't pollute the real cache.
_RDNS_CACHE_PATH = Path(os.environ.get("KAYAK_RDNS_CACHE", "/home/pat/kayak/var/rdns_cache.json"))
# Drop cache entries whose last-seen is older than this. Keeps the file
# bounded by recent activity instead of growing forever.
_RDNS_CACHE_TTL_DAYS = 60
_rdns_cache_loaded: bool = False


def _load_rdns_cache_from_disk() -> None:
    """Load `{ip: [name, last_seen_epoch]}` entries from disk, evicting stale.

    Tolerates the legacy `{ip: name_string}` shape too (treat as last-seen
    now, will get pruned in due course if not re-seen).
    """
    global _rdns_cache_loaded
    if _rdns_cache_loaded:
        return
    _rdns_cache_loaded = True
    try:
        with open(_RDNS_CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    cutoff = now_ts - _RDNS_CACHE_TTL_DAYS * 86400
    for ip, value in data.items():
        if not isinstance(ip, str):
            continue
        name = ""
        ts = now_ts
        if isinstance(value, list) and len(value) == 2:
            n, t = value
            if isinstance(n, str) and isinstance(t, int):
                name, ts = n, t
            else:
                continue
        elif isinstance(value, str):
            # Legacy shape: assume just-seen so we don't immediately evict.
            name = value
        else:
            continue
        if ts < cutoff:
            continue
        _rdns_cache.setdefault(ip, name)
        _rdns_last_seen.setdefault(ip, ts)


def _save_rdns_cache_to_disk() -> None:
    try:
        _RDNS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = {
            ip: [name, _rdns_last_seen.get(ip, 0)]
            for ip, name in _rdns_cache.items()
            if ip in _rdns_last_seen  # don't persist what we haven't seen this run *or* last
            or _rdns_last_seen.get(ip, 0) > 0
        }
        # The filter above keeps last-seen from disk (loaded into _rdns_last_seen)
        # plus anything we touched this run. Anything not in _rdns_last_seen at all
        # is an in-process-only artefact and not worth persisting.
        tmp = _RDNS_CACHE_PATH.with_suffix(_RDNS_CACHE_PATH.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"), sort_keys=True)
        os.replace(tmp, _RDNS_CACHE_PATH)
    except OSError:
        pass


def _rdns_lookup(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ""


def warm_rdns(
    ips: Iterable[str],
    *,
    workers: int = _RDNS_WORKERS,
    budget_s: float = _RDNS_TOTAL_BUDGET_S,
) -> None:
    """Pre-resolve reverse DNS for the given IPs in parallel under a total budget.

    Hydrates from an on-disk JSON cache first so daily renders only need to
    look up newly-seen IPs. Unresolved IPs (timed out) are NOT cached — only
    confirmed lookups (success or OSError, both via _rdns_lookup) get persisted,
    so a timeout this run gets re-tried next run. Updates a last-seen stamp
    on every IP we see so entries don't expire while the IP keeps appearing.
    """
    _load_rdns_cache_from_disk()
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    ip_list = list({ip for ip in ips})
    # Refresh last-seen for every IP in this run, cached or not. Lookups for
    # uncached IPs happen below; cached IPs just need their stamp bumped.
    for ip in ip_list:
        _rdns_last_seen[ip] = now_ts
    # Targets = IPs we haven't cached AND haven't already tried this process.
    # The "already tried" filter matters because the render calls warm_rdns
    # three times (one from each of run_chunked/run_paths/run_humans). Without
    # it, IPs that timed out on call #1 would cost another full budget on
    # call #2 and #3.
    targets = sorted([ip for ip in ip_list if ip not in _rdns_cache and ip not in _rdns_attempted])
    if not targets:
        _save_rdns_cache_to_disk()
        return
    _rdns_attempted.update(targets)
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rdns")
    futures = {ip: ex.submit(_rdns_lookup, ip) for ip in targets}
    done, _pending = concurrent.futures.wait(futures.values(), timeout=budget_s)
    for ip, fut in futures.items():
        if fut in done:
            try:
                _rdns_cache[ip] = fut.result()
            except Exception:
                # _rdns_lookup itself catches OSError; this branch is reachable
                # only on truly unexpected errors. Treat as "no PTR" and cache.
                _rdns_cache[ip] = ""
        # Else: future didn't finish before the budget expired. Leave it OUT
        # of the cache so next run takes another swing.
    ex.shutdown(wait=False, cancel_futures=True)
    _save_rdns_cache_to_disk()


def rdns(ip: str) -> str:
    """Return the cached PTR for *ip*, or ``""`` if not warmed yet.

    Crucially, never falls through to a SYNCHRONOUS gethostbyaddr — that
    historically blocked the main thread for ~10s per unwarmed IP, turning
    a render with thousands of unresolved IPs into an hours-long stall.
    Callers should call warm_rdns() with the IP set first; anything that
    didn't make it into the cache during the budgeted parallel sweep is
    treated as "unknown" rather than triggering a blocking re-lookup.
    """
    return _rdns_cache.get(ip, "")


def _is_uptrends(ip: str, paths: set[str], ua: str) -> bool:
    if ".uptrends.net" in rdns(ip):
        return True
    return list(paths) == ["/"] and ua == UPTRENDS_UA


def _is_scanner(ua: str, paths: set[str]) -> bool:
    if ua == FAKE_MSIE7:
        return True
    if (
        paths
        and paths.issubset(SCANNER_ONLY_PATHS | {"/"})
        and any(p in SCANNER_ONLY_PATHS for p in paths)
    ):
        return True
    return paths == {"/robots.txt"}


_ROOT_HAMMER_MIN_HITS = 3


def _is_root_hammer(paths_counter: collections.Counter[str], hits: int) -> bool:
    """An IP that hits only ``/`` and never touches an asset is bot-shaped.

    Real browsers fetch ``/style.css``, ``/static/*.js``, sparklines etc.
    along with the HTML, and revisits show up as conditional GETs (304s)
    in the access log too — so seeing ``paths == {"/"}`` after multiple
    hits means no real browser was on the other end. The threshold (3)
    leaves enough headroom for an odd lynx user or a deep-bookmarked
    refresh while still catching the IPv6-datacenter and 45.148.* scanners
    that hammer ``/`` hundreds-to-thousands of times in 24h.

    Don't use a UA-shape heuristic: modern privacy modes (iCloud Private
    Relay, Firefox RFP) deliberately strip UA tokens, so a "truncated"
    UA isn't a reliable bot indicator.
    """
    return set(paths_counter) == {"/"} and hits >= _ROOT_HAMMER_MIN_HITS


def _classify_ip(
    ip: str,
    ua: str,
    paths: set[str],
    *,
    paths_counter: collections.Counter[str] | None = None,
    hits: int = 0,
) -> str:
    if ip.startswith("207."):
        return "self"
    if monitors.is_betterstack(ip):
        return "monitor"
    if _BOT_RE.search(ua):
        return "bot"
    if _is_uptrends(ip, paths, ua):
        return "uptrends"
    if _is_scanner(ua, paths):
        return "scanner"
    if paths_counter is not None and _is_root_hammer(paths_counter, hits):
        # Catches the "lazy bot": only hits ``/`` repeatedly, never fetches an
        # asset. Distinct from "scanner" (which probes /.env, /wp-login.php,
        # …) and from "monitor" (which would be in the betterstack IP list).
        return "root-only"
    return "human"


def _ua_tag(ua: str) -> str:
    if "iPhone" in ua:
        return "iOS Safari"
    if "Android" in ua and "Firefox" in ua:
        return "Android FF"
    if "Android" in ua:
        return "Android Chrome"
    if "Macintosh" in ua and "Chrome" not in ua:
        return "Mac Safari"
    if "OPR/" in ua:
        return "Opera"
    if "Firefox" in ua:
        return "Firefox"
    if "Chrome" in ua:
        return "Chrome"
    return ua[:30]


# ----------------------------------------------------------------------
# Sub-command bodies
# ----------------------------------------------------------------------


class _IpRecord:
    __slots__ = ("first", "hits", "last", "paths", "ua")

    def __init__(self) -> None:
        self.hits: int = 0
        self.paths: collections.Counter[str] = collections.Counter()
        self.ua: str = ""
        self.first: dt.datetime | None = None
        self.last: dt.datetime | None = None


def _build_per_ip(events: Iterable[AccessEvent]) -> dict[str, _IpRecord]:
    per_ip: dict[str, _IpRecord] = {}
    for ev in events:
        rec = per_ip.setdefault(ev.client, _IpRecord())
        rec.hits += 1
        route = (ev.path or "").split("?", 1)[0]
        rec.paths[route] += 1
        if not rec.ua:
            rec.ua = ev.ua
        if rec.first is None or ev.ts < rec.first:
            rec.first = ev.ts
        rec.last = ev.ts
    return per_ip


def run_humans(
    hours: int,
    tz: dt.tzinfo,
    access_log_glob: str = "/var/log/nginx/*access.log*",
) -> str:
    now = dt.datetime.now(tz)
    cutoff = now - dt.timedelta(hours=hours)
    events = iter_access_events(since=cutoff, log_glob=access_log_glob)
    per_ip = _build_per_ip(events)

    warm_rdns(per_ip.keys())
    classifications = {
        ip: _classify_ip(ip, rec.ua, set(rec.paths), paths_counter=rec.paths, hits=rec.hits)
        for ip, rec in per_ip.items()
    }
    humans = [(ip, rec) for ip, rec in per_ip.items() if classifications[ip] == "human"]
    dropped: dict[str, int] = collections.Counter()
    for ip, rec in per_ip.items():
        cls = classifications[ip]
        if cls != "human":
            dropped[cls] += rec.hits

    lines = [
        f"# Distinct human visitors ({hours}h)",
        "",
        f"Window: {cutoff:%Y-%m-%d %H:%M %z} → {now:%Y-%m-%d %H:%M %z}",
        "",
        f"- **{len(humans)}** distinct human-looking IPs",
        f"- {sum(r.hits for _, r in humans)} total human hits",
        f"- Filtered out: {dict(dropped)} "
        f"({sum(dropped.values())} hits across {len(per_ip) - len(humans)} IPs)",
        "",
        "| IP | country | hits | paths | span (h) | rdns | UA |",
        "|---|---|---|---|---|---|---|",
    ]
    for ip, rec in sorted(humans, key=lambda kv: -kv[1].hits):
        span = (rec.last - rec.first).total_seconds() / 3600.0 if rec.first and rec.last else 0.0
        name = rdns(ip) or "-"
        country = geoip.lookup(ip)
        lines.append(
            f"| `{ip}` | {country} | {rec.hits} | {len(rec.paths)} | "
            f"{span:.1f} | {name} | {_ua_tag(rec.ua)} |"
        )
    geoip.flush_cache()
    return "\n".join(lines) + "\n"


class _Bucket:
    """Per-bucket aggregator for run_chunked — typed beats dict[str, object]."""

    __slots__ = ("bot_hits", "human_hits", "human_ips", "other_hits")

    def __init__(self) -> None:
        self.human_hits = 0
        self.bot_hits = 0
        self.other_hits = 0
        self.human_ips: set[str] = set()


def run_paths(
    hours: int,
    tz: dt.tzinfo,
    access_log_glob: str = "/var/log/nginx/*access.log*",
    *,
    top: int = 40,
) -> str:
    """Markdown table of top-N URL paths by hits over the window.

    Query strings are stripped (``/description.php?id=217`` and
    ``/description.php?id=42`` both count under ``/description.php``).
    Hits are partitioned into human / bot / other using the same
    classifier as :func:`run_humans` and :func:`run_chunked`, so noise
    paths (`/`-hammer scanners, `/.env`-style probes) show up under
    "other" and the human column reflects what real visitors actually
    looked at.
    """
    now = dt.datetime.now(tz)
    cutoff = now - dt.timedelta(hours=hours)

    per_ip: dict[str, _IpRecord] = {}
    per_path_by_ip: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    for ev in iter_access_events(since=cutoff, log_glob=access_log_glob):
        rec = per_ip.setdefault(ev.client, _IpRecord())
        rec.hits += 1
        route = (ev.path or "").split("?", 1)[0]
        rec.paths[route] += 1
        per_path_by_ip[ev.client][route] += 1
        if not rec.ua:
            rec.ua = ev.ua
        if rec.first is None or ev.ts < rec.first:
            rec.first = ev.ts
        rec.last = ev.ts

    warm_rdns(per_ip.keys())
    classifications = {
        ip: _classify_ip(ip, rec.ua, set(rec.paths), paths_counter=rec.paths, hits=rec.hits)
        for ip, rec in per_ip.items()
    }

    # Roll per-IP path counts into per-path bucket counts, dropping asset
    # requests (CSS/JS/JSON/PNG/etc.) — those ride along with every page
    # load and aren't pages a user navigates to.
    human_by_path: collections.Counter[str] = collections.Counter()
    bot_by_path: collections.Counter[str] = collections.Counter()
    other_by_path: collections.Counter[str] = collections.Counter()
    for ip, paths in per_path_by_ip.items():
        cls = classifications.get(ip, "human")
        target = human_by_path if cls == "human" else bot_by_path if cls == "bot" else other_by_path
        for path, count in paths.items():
            if _is_asset_path(path):
                continue
            target[path] += count

    all_paths = set(human_by_path) | set(bot_by_path) | set(other_by_path)
    rows = sorted(
        (
            (
                path,
                human_by_path[path],
                bot_by_path[path],
                other_by_path[path],
            )
            for path in all_paths
        ),
        key=lambda r: -(r[1] + r[2] + r[3]),
    )[:top]

    total_h = sum(human_by_path.values())
    total_b = sum(bot_by_path.values())
    total_o = sum(other_by_path.values())

    lines = [
        f"# Hits by URL path ({hours}h, top {top})",
        "",
        (
            f"Window: {cutoff:%Y-%m-%d %H:%M %z} → {now:%Y-%m-%d %H:%M %z}. "
            f"Totals: {total_h + total_b + total_o} hits "
            f"({total_h} human, {total_b} bot, {total_o} other). "
            f"Query strings stripped; assets (.css/.js/.json/.geojson/images/fonts) "
            f"filtered out — pages only."
        ),
        "",
        "| path | human | bot | other | total |",
        "|---|---|---|---|---|",
    ]
    for path, h, b, o in rows:
        lines.append(f"| `{path}` | {h} | {b} | {o} | {h + b + o} |")
    return "\n".join(lines) + "\n"


def _country_label(code: str, name: str) -> str:
    """Markdown cell text for a country: full name + bracketed ISO code."""
    if not code or code == "-":
        return "—"
    if not name:
        return code
    return f"{name} ({code})"


def run_countries(
    hours: int,
    tz: dt.tzinfo,
    access_log_glob: str = "/var/log/nginx/*access.log*",
    *,
    top: int = 25,
) -> str:
    """Markdown table of top-N countries by hits, broken out human/bot/other.

    Countries come from :mod:`kayak.analytics.geoip` (DB-IP City Lite mmdb)
    and render as ``Full Name (ISO-CC)`` for scanability. IPs we can't
    geo-locate collapse into a single ``—`` row. Hits are partitioned by the
    same classifier as :func:`run_humans`, so a country with a ton of "bot"
    hits and very few "human" usually means scanner traffic from a hosting
    hub (NL/RU/DE), not real visitors.
    """
    now = dt.datetime.now(tz)
    cutoff = now - dt.timedelta(hours=hours)

    per_ip: dict[str, _IpRecord] = {}
    for ev in iter_access_events(since=cutoff, log_glob=access_log_glob):
        rec = per_ip.setdefault(ev.client, _IpRecord())
        rec.hits += 1
        route = (ev.path or "").split("?", 1)[0]
        rec.paths[route] += 1
        if not rec.ua:
            rec.ua = ev.ua
        if rec.first is None or ev.ts < rec.first:
            rec.first = ev.ts
        rec.last = ev.ts

    warm_rdns(per_ip.keys())
    classifications = {
        ip: _classify_ip(ip, rec.ua, set(rec.paths), paths_counter=rec.paths, hits=rec.hits)
        for ip, rec in per_ip.items()
    }

    # Keyed by (code, name) so the rendered label can show both.
    human_by_country: collections.Counter[tuple[str, str]] = collections.Counter()
    bot_by_country: collections.Counter[tuple[str, str]] = collections.Counter()
    other_by_country: collections.Counter[tuple[str, str]] = collections.Counter()
    human_ips_by_country: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for ip, rec in per_ip.items():
        key = (geoip.lookup(ip), geoip.lookup_name(ip))
        cls = classifications.get(ip, "human")
        if cls == "human":
            human_by_country[key] += rec.hits
            human_ips_by_country[key].add(ip)
        elif cls == "bot":
            bot_by_country[key] += rec.hits
        else:
            other_by_country[key] += rec.hits

    geoip.flush_cache()

    all_countries = set(human_by_country) | set(bot_by_country) | set(other_by_country)
    rows = sorted(
        (
            (
                key,
                human_by_country[key],
                len(human_ips_by_country[key]),
                bot_by_country[key],
                other_by_country[key],
            )
            for key in all_countries
        ),
        key=lambda r: -(r[1] + r[3] + r[4]),
    )[:top]

    total_h = sum(human_by_country.values())
    total_b = sum(bot_by_country.values())
    total_o = sum(other_by_country.values())

    lines = [
        f"# Hits by country ({hours}h, top {top})",
        "",
        (
            f"Window: {cutoff:%Y-%m-%d %H:%M %z} → {now:%Y-%m-%d %H:%M %z}. "
            f"Totals: {total_h + total_b + total_o} hits "
            f"({total_h} human, {total_b} bot, {total_o} other). "
            f"`—` = IP couldn't be geo-located (private, reserved, or absent from DB-IP)."
        ),
        "",
        "| country | human hits | human IPs | bot | other | total |",
        "|---|---|---|---|---|---|",
    ]
    for (code, name), h, hips, b, o in rows:
        lines.append(f"| {_country_label(code, name)} | {h} | {hips} | {b} | {o} | {h + b + o} |")
    return "\n".join(lines) + "\n"


def run_subdivisions(
    hours: int,
    tz: dt.tzinfo,
    access_log_glob: str = "/var/log/nginx/*access.log*",
    *,
    top: int = 25,
    countries: tuple[str, ...] = ("US", "CA"),
) -> str:
    """Markdown table of state / province hits for the named countries.

    DB-IP Lite carries first-level subdivisions for many countries; this
    function defaults to US states and Canadian provinces but accepts any
    country code list. IPs whose country isn't in ``countries`` (or whose
    subdivision is empty) are skipped. Otherwise the shape mirrors
    :func:`run_countries`: per-bucket hits + distinct-human-IPs.
    """
    now = dt.datetime.now(tz)
    cutoff = now - dt.timedelta(hours=hours)
    wanted = {c.upper() for c in countries}

    per_ip: dict[str, _IpRecord] = {}
    for ev in iter_access_events(since=cutoff, log_glob=access_log_glob):
        rec = per_ip.setdefault(ev.client, _IpRecord())
        rec.hits += 1
        route = (ev.path or "").split("?", 1)[0]
        rec.paths[route] += 1
        if not rec.ua:
            rec.ua = ev.ua
        if rec.first is None or ev.ts < rec.first:
            rec.first = ev.ts
        rec.last = ev.ts

    warm_rdns(per_ip.keys())
    classifications = {
        ip: _classify_ip(ip, rec.ua, set(rec.paths), paths_counter=rec.paths, hits=rec.hits)
        for ip, rec in per_ip.items()
    }

    # Key: (country_code, subdivision_name)
    human_hits: collections.Counter[tuple[str, str]] = collections.Counter()
    bot_hits: collections.Counter[tuple[str, str]] = collections.Counter()
    other_hits: collections.Counter[tuple[str, str]] = collections.Counter()
    human_ips: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    for ip, rec in per_ip.items():
        cc = geoip.lookup(ip)
        if cc not in wanted:
            continue
        sub = geoip.lookup_subdivision(ip)
        if not sub:
            continue
        key = (cc, sub)
        cls = classifications.get(ip, "human")
        if cls == "human":
            human_hits[key] += rec.hits
            human_ips[key].add(ip)
        elif cls == "bot":
            bot_hits[key] += rec.hits
        else:
            other_hits[key] += rec.hits

    geoip.flush_cache()

    all_keys = set(human_hits) | set(bot_hits) | set(other_hits)
    rows = sorted(
        (
            (
                key,
                human_hits[key],
                len(human_ips[key]),
                bot_hits[key],
                other_hits[key],
            )
            for key in all_keys
        ),
        key=lambda r: -(r[1] + r[3] + r[4]),
    )[:top]

    label = " / ".join(sorted(wanted))
    total_h = sum(human_hits.values())
    total_b = sum(bot_hits.values())
    total_o = sum(other_hits.values())

    lines = [
        f"# {label} states & provinces ({hours}h, top {top})",
        "",
        (
            f"Window: {cutoff:%Y-%m-%d %H:%M %z} → {now:%Y-%m-%d %H:%M %z}. "
            f"Filtered to {label}; subdivision data from DB-IP City Lite. "
            f"Totals: {total_h + total_b + total_o} hits "
            f"({total_h} human, {total_b} bot, {total_o} other)."
        ),
        "",
        "| subdivision | human hits | human IPs | bot | other | total |",
        "|---|---|---|---|---|---|",
    ]
    for (cc, sub), h, hips, b, o in rows:
        lines.append(f"| {sub} ({cc}) | {h} | {hips} | {b} | {o} | {h + b + o} |")
    return "\n".join(lines) + "\n"


def run_chunked(
    hours: int,
    bucket_hours: int,
    tz: dt.tzinfo,
    access_log_glob: str = "/var/log/nginx/*access.log*",
) -> str:
    now = dt.datetime.now(tz)
    cutoff = now - dt.timedelta(hours=hours)

    raw: dict[str, list[AccessEvent]] = collections.defaultdict(list)
    per_ip: dict[str, _IpRecord] = {}
    for ev in iter_access_events(since=cutoff, log_glob=access_log_glob):
        raw[ev.client].append(ev)
        rec = per_ip.setdefault(ev.client, _IpRecord())
        rec.hits += 1
        route = (ev.path or "").split("?", 1)[0]
        rec.paths[route] += 1
        if not rec.ua:
            rec.ua = ev.ua
        if rec.first is None or ev.ts < rec.first:
            rec.first = ev.ts
        rec.last = ev.ts

    warm_rdns(per_ip.keys())
    classifications = {
        ip: _classify_ip(ip, rec.ua, set(rec.paths), paths_counter=rec.paths, hits=rec.hits)
        for ip, rec in per_ip.items()
    }
    buckets = _bucket_events(raw, classifications, now, bucket_hours)

    return _render_chunked(
        hours=hours,
        bucket_hours=bucket_hours,
        cutoff=cutoff,
        now=now,
        per_ip=per_ip,
        classifications=classifications,
        buckets=buckets,
    )


def _bucket_events(
    raw: dict[str, list[AccessEvent]],
    classifications: dict[str, str],
    now: dt.datetime,
    bucket_hours: int,
) -> dict[dt.datetime, _Bucket]:
    """Group events into N-hour buckets, anchored so the most recent ends at ``now``."""

    def bucket_start(ts: dt.datetime) -> dt.datetime:
        delta = (now - ts).total_seconds()
        idx = int(delta // (bucket_hours * 3600))
        return now - dt.timedelta(hours=bucket_hours * (idx + 1))

    buckets: dict[dt.datetime, _Bucket] = collections.defaultdict(_Bucket)
    for ip, ev_list in raw.items():
        cls = classifications[ip]
        for ev in ev_list:
            b = buckets[bucket_start(ev.ts)]
            if cls == "human":
                b.human_hits += 1
                b.human_ips.add(ip)
            elif cls == "bot":
                b.bot_hits += 1
            else:
                b.other_hits += 1
    return buckets


def _render_chunked(
    hours: int,
    bucket_hours: int,
    cutoff: dt.datetime,
    now: dt.datetime,
    per_ip: dict[str, _IpRecord],
    classifications: dict[str, str],
    buckets: dict[dt.datetime, _Bucket],
) -> str:
    lines = [
        f"# Human / bot traffic, {bucket_hours}h buckets ({hours}h)",
        "",
        f"Window: {cutoff:%Y-%m-%d %H:%M %z} → {now:%Y-%m-%d %H:%M %z}",
        "",
        "| bucket start | humans | bots | other | distinct human IPs |",
        "|---|---|---|---|---|",
    ]
    for b_ts in sorted(buckets):
        b = buckets[b_ts]
        lines.append(
            f"| {b_ts:%Y-%m-%d %H:%M} | {b.human_hits} | {b.bot_hits} | "
            f"{b.other_hits} | {len(b.human_ips)} |"
        )

    humans = [ip for ip, cls in classifications.items() if cls == "human"]
    bots = [ip for ip, cls in classifications.items() if cls == "bot"]
    others_hits: collections.Counter[str] = collections.Counter()
    for ip, rec in per_ip.items():
        if classifications[ip] in ("uptrends", "scanner", "self"):
            others_hits[classifications[ip]] += rec.hits
    lines.append("")
    lines.append(f"- Distinct human-looking IPs over {hours}h: **{len(humans)}**")
    lines.append(f"- Total human hits: {sum(per_ip[ip].hits for ip in humans)}")
    lines.append(f"- Total bot hits: {sum(per_ip[ip].hits for ip in bots)} (from {len(bots)} IPs)")
    lines.append(f"- Filtered: {dict(others_hits)}")
    return "\n".join(lines) + "\n"
