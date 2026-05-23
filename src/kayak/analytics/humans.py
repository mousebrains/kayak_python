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
import datetime as dt
import json
import os
import queue
import re
import socket
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from . import geoip, ip_reputation, monitors, privacy_relays
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
# IPs we've already taken a swing at this process. The status render calls
# warm_rdns six times (one per traffic report); this stops an IP from costing a
# fresh budget on each — only the first call sees it as a target.
# (Across processes, the negative cache + backoff below handles re-suppression.)
_rdns_attempted: set[str] = set()
# Epoch after which a negative (black-holed) cache entry becomes eligible for a
# fresh lookup, and the count of consecutive black-holes driving the backoff.
# Both are populated only for timed-out IPs; a confirmed result clears them.
_rdns_retry_after: dict[str, int] = {}
_rdns_fail_count: dict[str, int] = {}

# socket.gethostbyaddr is a blocking C call that ignores socket.setdefaulttimeout;
# a single unreachable resolver can hang it indefinitely. Resolve the whole IP set
# in parallel on *daemon* threads under a wall-clock budget: lookups still stuck
# at the deadline are abandoned (daemon threads don't block interpreter exit), and
# their IPs are negative-cached with a backed-off retry (see _RDNS_NEG_RETRY_BASE_S)
# so we don't re-stall on them every run. The budget MUST stay well under
# kayak-status.service's TimeoutStartSec, or systemd kills the render mid-sweep.
# Defaults sized for the nightly status render: ~4k IPs/day, of which ~60% have no
# PTR. The on-disk cache means only NEW IPs need a lookup on subsequent runs.
_RDNS_WORKERS = 128
_RDNS_TOTAL_BUDGET_S = 45.0
# Black-holed lookups (no answer within the budget) get a negative cache entry so
# they're skipped on the nightly render. The retry window backs off exponentially
# per consecutive failure — 7d, 14d, 28d, 56d, 112d — capped at ~26 weeks, so a
# transient resolver/IPv6 outage self-heals on the next attempt while a chronically
# unresolvable IP is probed ever less often instead of every run. A confirmed
# result (PTR or fast NXDOMAIN) resets the backoff and gets the full cache TTL.
_RDNS_NEG_RETRY_BASE_S = 7 * 86400
_RDNS_NEG_RETRY_MAX_S = 182 * 86400  # ~26 weeks

# Persistent on-disk rdns cache: subsequent renders only have to look up the
# IPs that are new since the last run. Path is overridable via env so tests
# don't pollute the real cache.
_RDNS_CACHE_PATH = Path(os.environ.get("KAYAK_RDNS_CACHE", "/home/pat/kayak/var/rdns_cache.json"))
# Drop cache entries whose last-seen is older than this (~26 weeks). Bounds the
# file by recent activity, and is long enough that a fully backed-off negative
# entry (retry up to ~26 weeks out) isn't evicted before its retry comes due, for
# an IP that keeps reappearing in the logs.
_RDNS_CACHE_TTL_DAYS = 182  # ~26 weeks
_rdns_cache_loaded: bool = False


def _parse_rdns_entry(value: object) -> tuple[str, int, int, int] | None:
    """Parse one on-disk cache value into ``(name, last_seen, retry_after,
    fail_count)``, or ``None`` for an unrecognized shape.

    ``retry_after``/``fail_count`` are 0 for confirmed (2-element) and legacy
    string entries. A legacy entry reports ``last_seen == 0`` so the caller can
    substitute "just seen"; a real entry always has a positive epoch.
    """
    if isinstance(value, str):
        return value, 0, 0, 0  # legacy name-only shape
    if isinstance(value, list) and len(value) >= 2:
        name, ts = value[0], value[1]
        if not (isinstance(name, str) and isinstance(ts, int)):
            return None
        retry_after = value[2] if len(value) >= 3 and isinstance(value[2], int) else 0
        fail_count = value[3] if len(value) >= 4 and isinstance(value[3], int) else 0
        # A negative entry implies >=1 failure; keep the pair coherent so a later
        # re-fail escalates the backoff instead of resetting to tier 1.
        if retry_after and not fail_count:
            fail_count = 1
        return name, ts, retry_after, fail_count
    return None


def _store_rdns_entry(ip: str, name: str, ts: int, retry_after: int, fail_count: int) -> None:
    """Hydrate the in-memory rdns dicts from one parsed cache entry."""
    _rdns_cache.setdefault(ip, name)
    _rdns_last_seen.setdefault(ip, ts)
    if retry_after:
        _rdns_retry_after.setdefault(ip, retry_after)
    if fail_count:
        _rdns_fail_count.setdefault(ip, fail_count)


def _load_rdns_cache_from_disk() -> None:
    """Load cached rDNS entries from disk, evicting only TTL-expired ones.

    On-disk value is ``[name, last_seen_epoch]`` for a confirmed result, or
    ``[name, last_seen_epoch, retry_after_epoch, fail_count]`` for a negative
    (black-holed) entry. ``warm_rdns`` re-probes a negative once
    ``retry_after_epoch`` passes; ``fail_count`` drives the backoff escalation.
    Tolerates the legacy ``{ip: name_string}`` shape too (treated as just-seen so
    it isn't evicted immediately).
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
        parsed = _parse_rdns_entry(value)
        if parsed is None:
            continue
        name, ts, retry_after, fail_count = parsed
        if ts == 0:
            ts = now_ts  # legacy entry: treat as just-seen so we don't evict it
        if ts < cutoff:
            continue
        # Load negatives (incl. expired ones) so their backoff state survives;
        # warm_rdns decides whether the retry window has opened.
        _store_rdns_entry(ip, name, ts, retry_after, fail_count)


def _save_rdns_cache_to_disk() -> None:
    try:
        _RDNS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Persist entries we've seen this run or loaded from disk (anything in
        # _rdns_last_seen). A negative (black-holed) entry carries its retry-after
        # + fail_count so the backoff survives restarts; a confirmed entry stays
        # the compact [name, ts] shape.
        out: dict[str, list[str | int]] = {}
        for ip, name in _rdns_cache.items():
            if ip not in _rdns_last_seen:
                continue
            ts = _rdns_last_seen[ip]
            ra = _rdns_retry_after.get(ip, 0)
            if ra:
                out[ip] = [name, ts, ra, _rdns_fail_count.get(ip, 1)]
            else:
                out[ip] = [name, ts]
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


def _resolve_parallel(
    targets: list[str], workers: int, budget_s: float
) -> tuple[dict[str, str], set[str]]:
    """Resolve PTRs for *targets* on daemon threads under a wall-clock budget.

    Returns ``(resolved, started)``: ``resolved`` maps each IP that *completed*
    within the budget to its name (``""`` for a confirmed no-PTR); ``started`` is
    every IP a worker actually began looking up. So ``started - resolved`` is the
    set that black-holed (a worker is still blocked in ``socket.gethostbyaddr`` at
    the deadline), while ``targets - started`` were never pulled off the queue
    because the pool saturated on black-holes — the caller must NOT penalize those
    or it would negative-cache valid PTRs it never tried. Workers are daemon
    threads, so a stuck lookup is abandoned at the deadline and can't pin
    interpreter exit (the bug that timed out kayak-status.service).
    """
    work: queue.SimpleQueue[str] = queue.SimpleQueue()
    for ip in targets:
        work.put(ip)
    resolved: dict[str, str] = {}
    started: set[str] = set()
    lock = threading.Lock()

    def _worker() -> None:
        while True:
            try:
                ip = work.get_nowait()
            except queue.Empty:
                return
            with lock:
                started.add(ip)
            name = _rdns_lookup(ip)  # may block past the deadline; we're daemon
            with lock:
                resolved[ip] = name

    threads = [
        threading.Thread(target=_worker, name=f"rdns-{i}", daemon=True)
        for i in range(min(workers, len(targets)))
    ]
    for t in threads:
        t.start()
    deadline = time.monotonic() + budget_s
    for t in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        t.join(timeout=remaining)
    # Atomic snapshot of both — daemon workers stuck past the deadline may still
    # be mutating them; anything that lands after this copy is simply ignored.
    with lock:
        return dict(resolved), set(started)


def warm_rdns(
    ips: Iterable[str],
    *,
    workers: int = _RDNS_WORKERS,
    budget_s: float = _RDNS_TOTAL_BUDGET_S,
) -> None:
    """Pre-resolve reverse DNS for the given IPs in parallel under a total budget.

    Hydrates from the on-disk cache first so daily renders only look up IPs that
    are new or whose negative-cache backoff has expired. Lookups run on *daemon*
    threads, so any that black-hole inside ``socket.gethostbyaddr`` (a blocking C
    call that ignores socket timeouts) are abandoned at the budget deadline and
    never block interpreter exit — the bug that timed out kayak-status.service.
    Every attempted IP is then cached: a confirmed result (PTR or fast NXDOMAIN)
    persists for the full TTL and clears any backoff; a black-hole gets a negative
    entry whose retry window backs off exponentially (``_RDNS_NEG_RETRY_BASE_S`` ..
    ``_RDNS_NEG_RETRY_MAX_S``), so we stop re-stalling on the same dead IPs.
    """
    _load_rdns_cache_from_disk()
    now_ts = int(dt.datetime.now(dt.UTC).timestamp())
    ip_list = list({ip for ip in ips})
    # Refresh last-seen for every IP in this run, cached or not, so entries don't
    # expire while the IP keeps appearing.
    for ip in ip_list:
        _rdns_last_seen[ip] = now_ts
    # Targets = IPs that are uncached, or negative-cached past their retry window,
    # AND not already tried this process. The "already tried" filter matters
    # because the status render calls warm_rdns six times (chunked/countries/
    # subdivisions/asns/paths/humans); without it, IPs that stalled on call #1
    # would cost another full budget on every later call.
    targets = sorted(
        ip
        for ip in ip_list
        if ip not in _rdns_attempted
        and (ip not in _rdns_cache or (ip in _rdns_retry_after and now_ts >= _rdns_retry_after[ip]))
    )
    if not targets:
        _save_rdns_cache_to_disk()
        return
    _rdns_attempted.update(targets)

    resolved, started = _resolve_parallel(targets, workers, budget_s)
    for ip, name in resolved.items():
        _rdns_cache[ip] = name
        _rdns_retry_after.pop(ip, None)
        _rdns_fail_count.pop(ip, None)
    # Negative-cache only IPs a worker actually started but that didn't finish
    # (genuine black-holes), with an exponentially backed-off retry. IPs never
    # pulled off the queue (pool saturated) are left uncached so next run retries
    # them without a backoff penalty — they may well have valid PTRs.
    for ip in started - resolved.keys():
        n = _rdns_fail_count.get(ip, 0) + 1
        interval = min(_RDNS_NEG_RETRY_BASE_S * 2 ** (n - 1), _RDNS_NEG_RETRY_MAX_S)
        _rdns_cache[ip] = ""
        _rdns_retry_after[ip] = now_ts + interval
        _rdns_fail_count[ip] = n
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


_NO_ASSETS_MIN_HITS = 2


def _is_data_feed_only(paths_counter: collections.Counter[str], hits: int) -> bool:
    """An IP that fetches only the sparklines JSON feed is a data-feed scraper.

    ``/static/sparklines.json`` is loaded by every kayak HTML page via a
    ``<script>`` reference; a real browser never reaches that endpoint
    without first fetching the page that referenced it. So an IP whose
    entire path-set is ``{sparklines.json}`` (plus optionally
    ``/favicon.ico`` — browsers and some scrapers auto-probe favicon) is
    unambiguously a scraper pulling the feed directly for the river-level
    data inside.

    Strict subset on the path-set: if the IP ALSO hit ``/`` or any other
    HTML/PHP page, the rule doesn't fire and the IP falls through to
    no-assets / human as appropriate. ``hits >= 1`` is enough — even a
    single sparklines-only hit is bot-shaped (real users don't bookmark
    raw JSON feeds).
    """
    if hits < 1:
        return False
    allowed = {"/static/sparklines.json", "/favicon.ico"}
    if not set(paths_counter).issubset(allowed):
        return False
    return "/static/sparklines.json" in paths_counter


def _is_no_browser_assets(paths_counter: collections.Counter[str], hits: int) -> bool:
    """An IP with ≥2 hits but no ``.css`` or ``.js`` fetch is bot-shaped.

    Real browsers — even returning users with warm caches — almost always
    end up logging at least one ``.css`` or ``.js`` hit per 24 h window,
    because the kayak project cache-busts those files via query strings
    (``/static/levels.js?v=<mtime>``) and content-hashed filenames
    (``/static/style-<hash>.css``); any deploy that changes either bumps
    the URL and forces a re-fetch. Plus heavier pages (gauge.php,
    description.php, reach.php) pull in additional JS the home page
    doesn't (feature-map.js, plot-hover.js, leaflet.js), so a user who
    explores beyond ``/`` will keep producing fresh JS hits.

    Scrapers, by contrast, fetch the HTML/JSON they want and skip the
    browser scaffolding. The observed Singapore + Vietnam + Alibaba-
    Cloud botnet pattern is exactly this shape: hit ``/`` + maybe
    ``/static/sparklines.json`` once or twice, never a single ``.css``
    or ``.js`` byte. Same goes for the ``/cgi/png`` legacy-URL probers
    from Google Cloud US-Central.

    Threshold of 2 hits is low enough to catch the doubleton scrapers
    while still letting single-hit bouncing visitors through (we can't
    distinguish a one-hit bot from a real one-page bouncer).

    Don't use a UA-shape heuristic: modern privacy modes (iCloud Private
    Relay, Firefox RFP) deliberately strip UA tokens, so a "truncated"
    UA isn't a reliable bot indicator.

    Subsumes the earlier ``_is_root_hammer`` rule (paths == {"/"} and
    hits >= 3): that exact pattern still triggers under this broader
    check, plus the multi-path, gauge.php-only, and sparklines-only
    scrapers it used to miss.
    """
    if hits < _NO_ASSETS_MIN_HITS:
        return False
    for path in paths_counter:
        lower = path.lower().rstrip("/")
        if "." not in lower:
            continue
        ext = lower[lower.rfind(".") :]
        if ext in (".css", ".js"):
            return False
    return True


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
    # Apple Private Relay egress IPs run real Safari users (verified via the
    # UA distribution + Apple's published country/region hint). Short-circuit
    # to "human" before any of the bot / scanner / no-assets heuristics fire,
    # because relay rotates the egress IP per request — a single browsing
    # session's HTML, CSS, and JS hits split across different IPs, making
    # individual ones look incomplete (~11 % FP rate on no-assets before
    # this gate). The relay list is sourced from Apple's published CSV via
    # privacy_relays.is_apple_private_relay().
    if privacy_relays.is_apple_private_relay(ip):
        return "human"
    # FireHOL Level 1 community blocklist (dshield / spamhaus DROP / feodo /
    # fullbogons). High-confidence known-bad — bogons and known abuse IPs
    # have no legitimate reason to hit a regional kayak site. Checked AFTER
    # private_relay so a real Safari user via a Fastly egress that's
    # coincidentally on a list doesn't get blocklisted.
    if ip_reputation.is_firehol_blocked(ip):
        return "blocklisted"
    if _BOT_RE.search(ua):
        return "bot"
    if _is_uptrends(ip, paths, ua):
        return "uptrends"
    if _is_scanner(ua, paths):
        return "scanner"
    if paths_counter is not None and _is_data_feed_only(paths_counter, hits):
        # Tighter than no-assets: IPs that hit ONLY the sparklines JSON
        # feed (and optionally favicon) — real browsers never reach
        # /static/sparklines.json directly without loading the HTML page
        # that references it.
        return "data-feed"
    if paths_counter is not None and _is_no_browser_assets(paths_counter, hits):
        # Catches the "lazy bot": fetches HTML / JSON / PHP endpoints but
        # never loads a single .css or .js file, so no real browser was on
        # the other end. Distinct from "scanner" (which probes /.env,
        # /wp-login.php, …) and from "monitor" (in the Better Stack IP
        # list). Subsumes the earlier "root-only" bucket.
        return "no-assets"
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
        "| IP | country | org (AS) | hits | paths | span (h) | rdns | UA |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for ip, rec in sorted(humans, key=lambda kv: -kv[1].hits):
        span = (rec.last - rec.first).total_seconds() / 3600.0 if rec.first and rec.last else 0.0
        name = rdns(ip) or "-"
        # Apple Private Relay egress IPs are real Safari users — show
        # Apple's reported region (the user's actual location) and a
        # readable org label, since the DB-IP entry would just say
        # Fastly / Cloudflare which is the egress hop, not who's there.
        relay = privacy_relays.apple_relay_region(ip)
        if relay is not None:
            cc, region, city = relay
            country = cc or "-"
            org_label = (
                f"iCloud Private Relay — {city}, {region}"
                if region or city
                else "iCloud Private Relay"
            )
        else:
            country = geoip.lookup(ip)
            asn = geoip.lookup_asn(ip)
            org = geoip.lookup_asn_org(ip) or "-"
            org_label = f"{org} (AS{asn})" if asn else org
        lines.append(
            f"| `{ip}` | {country} | {org_label} | {rec.hits} | {len(rec.paths)} | "
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


def run_asns(
    hours: int,
    tz: dt.tzinfo,
    access_log_glob: str = "/var/log/nginx/*access.log*",
    *,
    top: int = 25,
) -> str:
    """Markdown table of top-N autonomous systems by hits, human/bot/other.

    Mirrors :func:`run_countries` but keyed off the ASN owner from
    :mod:`kayak.analytics.geoip` (DB-IP ASN Lite mmdb). Each row shows
    the organization name followed by the AS number — Alibaba (US)
    Technology Co., Ltd. (AS45102), Hetzner Online GmbH (AS213230), etc.
    IPs that can't be resolved to an ASN collapse into a single "(no ASN)"
    row. Useful for spotting datacenter-hosted scraper traffic the per-
    country view masks: a country like SG might look like a real
    audience until you see 90 % of its hits come from Alibaba Cloud.
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

    # Key: (asn_number, asn_org). Same ASN can have multiple org-name spellings
    # in edge cases, so the org name is part of the key for display fidelity.
    human_hits: collections.Counter[tuple[int, str]] = collections.Counter()
    bot_hits: collections.Counter[tuple[int, str]] = collections.Counter()
    other_hits: collections.Counter[tuple[int, str]] = collections.Counter()
    human_ips: dict[tuple[int, str], set[str]] = collections.defaultdict(set)
    for ip, rec in per_ip.items():
        key = (geoip.lookup_asn(ip), geoip.lookup_asn_org(ip))
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

    total_h = sum(human_hits.values())
    total_b = sum(bot_hits.values())
    total_o = sum(other_hits.values())

    lines = [
        f"# Hits by autonomous system ({hours}h, top {top})",
        "",
        (
            f"Window: {cutoff:%Y-%m-%d %H:%M %z} → {now:%Y-%m-%d %H:%M %z}. "
            f"ASN data from DB-IP ASN Lite. Totals: "
            f"{total_h + total_b + total_o} hits "
            f"({total_h} human, {total_b} bot, {total_o} other)."
        ),
        "",
        "| organization | human hits | human IPs | bot | other | total |",
        "|---|---|---|---|---|---|",
    ]
    for (asn, org), h, hips, b, o in rows:
        if asn:
            label = f"{org} (AS{asn})" if org else f"AS{asn}"
        else:
            label = "(no ASN)"
        lines.append(f"| {label} | {h} | {hips} | {b} | {o} | {h + b + o} |")
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
