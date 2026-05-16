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
import re
import socket
from collections.abc import Iterable

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


_rdns_cache: dict[str, str] = {}


def rdns(ip: str) -> str:
    if ip in _rdns_cache:
        return _rdns_cache[ip]
    try:
        name = socket.gethostbyaddr(ip)[0]
    except OSError:
        name = ""
    _rdns_cache[ip] = name
    return name


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


def _classify_ip(ip: str, ua: str, paths: set[str]) -> str:
    if ip.startswith("207."):
        return "self"
    if _BOT_RE.search(ua):
        return "bot"
    if _is_uptrends(ip, paths, ua):
        return "uptrends"
    if _is_scanner(ua, paths):
        return "scanner"
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

    classifications = {ip: _classify_ip(ip, rec.ua, set(rec.paths)) for ip, rec in per_ip.items()}
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
        "| IP | hits | paths | span (h) | rdns | UA |",
        "|---|---|---|---|---|---|",
    ]
    for ip, rec in sorted(humans, key=lambda kv: -kv[1].hits):
        span = (rec.last - rec.first).total_seconds() / 3600.0 if rec.first and rec.last else 0.0
        name = rdns(ip) or "-"
        lines.append(
            f"| `{ip}` | {rec.hits} | {len(rec.paths)} | {span:.1f} | {name} | {_ua_tag(rec.ua)} |"
        )
    return "\n".join(lines) + "\n"


class _Bucket:
    """Per-bucket aggregator for run_chunked — typed beats dict[str, object]."""

    __slots__ = ("bot_hits", "human_hits", "human_ips", "other_hits")

    def __init__(self) -> None:
        self.human_hits = 0
        self.bot_hits = 0
        self.other_hits = 0
        self.human_ips: set[str] = set()


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

    classifications = {ip: _classify_ip(ip, rec.ua, set(rec.paths)) for ip, rec in per_ip.items()}
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
