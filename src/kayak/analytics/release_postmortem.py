"""Release post-mortem analyzer.

Compares a baseline window (default 48h pre-release) against a
post-release window (default release → now) across nine signal
sources and emits a Markdown report to stdout.

Ported from ``~/logs.analyze/analyze.py`` (months-debugged against the
operator's actual log volume). The harvest-dir reads are replaced by
``_log_sources`` iterators; the 9 ``analyze_*`` functions are
otherwise verbatim.
"""

from __future__ import annotations

import collections
import datetime as dt
import re
from collections.abc import Iterable, Sequence
from urllib.parse import urlparse

from ._log_sources import (
    AccessEvent,
    CspEvent,
    ErrorEvent,
    UnitEvent,
    iter_access_events,
    iter_blocked_events,
    iter_csp_events,
    iter_error_events,
    iter_unit_events,
)
from ._release_context import (
    db_health_snapshot,
    deploy_paths_listing,
    git_log_since,
)

# ----------------------------------------------------------------------
# Generic helpers (verbatim from analyze.py)
# ----------------------------------------------------------------------


def route_of(path_q: str | None) -> str:
    if not path_q:
        return "-"
    return path_q.split("?", 1)[0]


def within(ts: dt.datetime, lo: dt.datetime, hi: dt.datetime) -> bool:
    return lo <= ts < hi


def fmt_ts(ts: dt.datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M %Z")


def per_hour(n: int, span: dt.timedelta) -> float:
    hours = max(span.total_seconds() / 3600.0, 1e-9)
    return n / hours


# Cluster-key normalization for error messages
_CLUSTER_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"public_html_\d+"), "public_html_<EPOCH>"),
    (re.compile(r"\bclient: \S+"), "client: <IP>"),
    (re.compile(r'\bupstream: "[^"]*"'), 'upstream: "<U>"'),
    (re.compile(r'\bhost: "[^"]*"'), 'host: "<H>"'),
    (re.compile(r"\bserver: \S+"), "server: <S>"),
    (re.compile(r'\brequest: "[^"]*"'), 'request: "<R>"'),
    (re.compile(r"\b\d+\.\d+\.\d+\.\d+\b"), "<IPv4>"),
    (re.compile(r"\b\d{3,}\b"), "<N>"),
    (re.compile(r"\s+"), " "),
]


def cluster_key(msg: str) -> str:
    s = msg
    for pat, repl in _CLUSTER_SUBS:
        s = pat.sub(repl, s)
    return s[:240].strip()


# ----------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------


def render_header(
    release: dt.datetime,
    baseline: tuple[dt.datetime, dt.datetime],
    post: tuple[dt.datetime, dt.datetime],
    git_commits: Sequence[str],
    db_health: dict[str, str],
    deploy_listing: Sequence[str],
) -> str:
    lines = ["# Post-release log analysis", ""]
    lines.append(f"- Release: **{fmt_ts(release)}**")
    lines.append(f"- Baseline: {fmt_ts(baseline[0])} → {fmt_ts(baseline[1])}")
    lines.append(f"- Post-release: {fmt_ts(post[0])} → {fmt_ts(post[1])}")
    lines.append("")
    lines.append("## Commits in window")
    lines.append("")
    if git_commits:
        lines.append("```")
        lines.extend(list(git_commits)[:20])
        if len(git_commits) > 20:
            lines.append(f"... and {len(git_commits) - 20} more")
        lines.append("```")
    else:
        lines.append("_(no commits in the analysis window)_")
    lines.append("")
    lines.append("## Deploy paths")
    lines.append("")
    if deploy_listing:
        lines.append("```")
        lines.extend(deploy_listing)
        lines.append("```")
    else:
        lines.append("_(no public_html* listings)_")
    lines.append("")
    lines.append("## DB health snapshot")
    lines.append("")
    if db_health:
        lines.append("```")
        for k, v in db_health.items():
            lines.append(f"{k:>32} = {v}")
        lines.append("```")
    else:
        lines.append("_(kayak.db not accessible)_")
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Analyzers (verbatim function bodies from analyze.py — only the input
# source changed from harvest globs to pre-materialized event lists)
# ----------------------------------------------------------------------


def analyze_systemd_units(  # noqa: C901 — ported from ~/logs.analyze/analyze.py; refactor deferred
    events: Iterable[UnitEvent],
    release: dt.datetime,
    post_hi: dt.datetime,
) -> str:
    lines = ["## Systemd units around release", ""]
    runs: dict[str, list[tuple[dt.datetime, str]]] = collections.defaultdict(list)
    failures_pre: list[UnitEvent] = []
    failures_post: list[UnitEvent] = []
    any_event = False
    for ev in events:
        any_event = True
        if ev.ts < release - dt.timedelta(hours=12) or ev.ts > post_hi:
            continue
        bucket = failures_post if ev.ts >= release else failures_pre
        if ".service" in ev.msg and (": Deactivated" in ev.msg or ": Failed" in ev.msg):
            m = re.match(r"(kayak-\S+\.service): (Deactivated successfully|Failed.*?)\.?$", ev.msg)
            if m:
                runs[m.group(1)].append((ev.ts, m.group(2)))
                if m.group(2).startswith("Failed"):
                    bucket.append(ev)
        elif ev.msg.startswith("ERROR") or ev.msg.startswith("CRITICAL"):
            bucket.append(ev)

    if not any_event:
        lines.append("_(no journal events from kayak-* units)_")
        return "\n".join(lines) + "\n"

    if failures_post:
        verdict = "FAIL"
    elif failures_pre:
        verdict = "WARN"
    else:
        verdict = "OK"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    if runs:
        lines.append("| unit | runs in window | last outcome |")
        lines.append("|---|---|---|")
        for unit, ev_list in sorted(runs.items()):
            last = ev_list[-1]
            lines.append(
                f"| {unit} | {len(ev_list)} | {last[1]} at {last[0].strftime('%m-%d %H:%M')} |"
            )
    else:
        lines.append("_(no unit lifecycle events found in the window)_")
    for label, bucket in (
        ("post-release", failures_post),
        ("pre-release (context)", failures_pre),
    ):
        if not bucket:
            continue
        lines.append("")
        lines.append(f"### Failure entries — {label}")
        lines.append("")
        lines.append("```")
        for ev in bucket[:20]:
            lines.append(f"{ev.ts.strftime('%Y-%m-%d %H:%M:%S')} {ev.unit}: {ev.msg}")
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def analyze_http_status(  # noqa: C901 — ported from ~/logs.analyze/analyze.py; refactor deferred
    events: Iterable[AccessEvent],
    baseline: tuple[dt.datetime, dt.datetime],
    post: tuple[dt.datetime, dt.datetime],
) -> str:
    pre: collections.Counter[tuple[str, int]] = collections.Counter()
    po: collections.Counter[tuple[str, int]] = collections.Counter()
    pre_total: collections.Counter[str] = collections.Counter()
    po_total: collections.Counter[str] = collections.Counter()
    for ev in events:
        route = route_of(ev.path)
        if within(ev.ts, *baseline):
            pre[(route, ev.status // 100)] += 1
            pre_total[route] += 1
        elif within(ev.ts, *post):
            po[(route, ev.status // 100)] += 1
            po_total[route] += 1

    lines = ["## HTTP status shift (kayak-access.log)", ""]
    if not pre_total and not po_total:
        lines.append("_(no kayak-access traffic in either window)_")
        return "\n".join(lines) + "\n"

    base_span = baseline[1] - baseline[0]
    post_span = post[1] - post[0]

    regressions: list[tuple[str, str]] = []
    routes = set(pre_total) | set(po_total)
    for r in routes:
        pre_5xx = pre[(r, 5)]
        po_5xx = po[(r, 5)]
        if po_5xx > 0 and pre_5xx == 0:
            regressions.append((r, f"new 5xx: {po_5xx} post vs 0 baseline"))
            continue
        pre_4xx_rate = per_hour(pre[(r, 4)], base_span) if pre_total[r] else 0.0
        po_4xx_rate = per_hour(po[(r, 4)], post_span) if po_total[r] else 0.0
        if po_4xx_rate > 0.5 and po_4xx_rate > pre_4xx_rate * 1.5 + 1.0:
            regressions.append(
                (r, f"4xx rate: {po_4xx_rate:.1f}/h post vs {pre_4xx_rate:.1f}/h baseline")
            )

    verdict = "OK" if not regressions else "WARN"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    if regressions:
        lines.append("### Routes with regressions")
        lines.append("")
        lines.append("| route | signal |")
        lines.append("|---|---|")
        for r, msg in sorted(regressions):
            lines.append(f"| `{r}` | {msg} |")
        lines.append("")

    top = po_total.most_common(12)
    lines.append("### Top routes post-release")
    lines.append("")
    lines.append("| route | baseline 2xx / 4xx / 5xx | post 2xx / 4xx / 5xx |")
    lines.append("|---|---|---|")
    for r, _ in top:
        b = f"{pre[(r, 2)]} / {pre[(r, 4)]} / {pre[(r, 5)]}"
        p = f"{po[(r, 2)]} / {po[(r, 4)]} / {po[(r, 5)]}"
        lines.append(f"| `{r}` | {b} | {p} |")
    lines.append("")
    return "\n".join(lines)


def analyze_error_clusters(
    events: Iterable[ErrorEvent],
    baseline: tuple[dt.datetime, dt.datetime],
    post: tuple[dt.datetime, dt.datetime],
) -> str:
    pre: collections.Counter[str] = collections.Counter()
    po: collections.Counter[str] = collections.Counter()
    samples: dict[str, ErrorEvent] = {}
    for ev in events:
        key = cluster_key(ev.msg)
        samples.setdefault(key, ev)
        if within(ev.ts, *baseline):
            pre[key] += 1
        elif within(ev.ts, *post):
            po[key] += 1

    lines = ["## nginx error clusters", ""]
    if not pre and not po:
        lines.append("_(no error-log entries in either window)_")
        return "\n".join(lines) + "\n"

    new_clusters = [k for k in po if pre[k] == 0]
    verdict = "OK" if not new_clusters else "WARN"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    lines.append(f"- Baseline: {sum(pre.values())} entries across {len(pre)} clusters")
    lines.append(f"- Post-release: {sum(po.values())} entries across {len(po)} clusters")
    lines.append("")
    if new_clusters:
        lines.append("### New error clusters (not seen in baseline)")
        lines.append("")
        for k in sorted(new_clusters, key=lambda k: -po[k]):
            ev = samples[k]
            lines.append(f"- **x{po[k]}** `[{ev.level}]` {k[:180]}")
        lines.append("")
    persistent = [(k, po[k], pre[k]) for k in po if pre[k] > 0]
    if persistent:
        lines.append("### Persistent clusters (both windows)")
        lines.append("")
        lines.append("| post | baseline | level | cluster |")
        lines.append("|---|---|---|---|")
        for k, npo, npre in sorted(persistent, key=lambda x: -x[1])[:10]:
            ev = samples[k]
            lines.append(f"| {npo} | {npre} | {ev.level} | `{k[:120]}` |")
        lines.append("")
    return "\n".join(lines)


def analyze_stale_deploy(
    events: Iterable[ErrorEvent],
    post: tuple[dt.datetime, dt.datetime],
    tz: dt.tzinfo,
) -> str:
    """Flag any error-log entry referencing a timestamped public_html_<N> dir."""
    hits = [ev for ev in events if "public_html_" in ev.msg]

    lines = ["## Stale staged-deploy-path probes", ""]
    if not hits:
        lines.append("**Verdict: OK** — no references to `public_html_<epoch>/` in error logs.")
        lines.append("")
        return "\n".join(lines) + "\n"

    by_epoch: dict[str, list[ErrorEvent]] = collections.defaultdict(list)
    for ev in hits:
        m = re.search(r"public_html_(\d+)", ev.msg)
        if m:
            by_epoch[m.group(1)].append(ev)

    in_window = [ev for ev in hits if within(ev.ts, *post)]
    verdict = "WARN" if in_window else "OK"
    lines.append(f"**Verdict: {verdict}**  ({len(hits)} total; {len(in_window)} post-release)")
    lines.append("")
    lines.append("| stale epoch | ts | hits | uniq clients | first request |")
    lines.append("|---|---|---|---|---|")
    for epoch, evs in sorted(by_epoch.items()):
        try:
            ts = dt.datetime.fromtimestamp(int(epoch), tz=tz)
        except (ValueError, OSError, OverflowError):
            ts = evs[0].ts
        clients = {e.client for e in evs if e.client}
        req = next((e.request for e in evs if e.request), "-")
        lines.append(
            f"| {epoch} ({ts.strftime('%m-%d %H:%M')}) | "
            f"{evs[0].ts.strftime('%m-%d %H:%M')} | {len(evs)} | "
            f"{len(clients)} | `{req}` |"
        )
    lines.append("")
    return "\n".join(lines)


def analyze_new_404s(  # noqa: C901 — ported from ~/logs.analyze/analyze.py; refactor deferred
    events: Iterable[AccessEvent],
    baseline: tuple[dt.datetime, dt.datetime],
    post: tuple[dt.datetime, dt.datetime],
) -> str:
    """Regression signal: path was 2xx/3xx in baseline, now 404 in post.

    Scanner probes for /.env.*, /.git/config etc. never had a 2xx baseline so
    they're excluded automatically — those live in blocked-access / 404 noise.
    """
    pre_ok: set[str] = set()
    pre_404: set[str] = set()
    po_404: collections.Counter[str] = collections.Counter()
    for ev in events:
        r = route_of(ev.path)
        if within(ev.ts, *baseline):
            if 200 <= ev.status < 400:
                pre_ok.add(r)
            elif ev.status == 404:
                pre_404.add(r)
        elif within(ev.ts, *post) and ev.status == 404:
            po_404[r] += 1
    regressions = [(r, n) for r, n in po_404.items() if r in pre_ok]
    novel = [(r, n) for r, n in po_404.items() if r not in pre_ok and r not in pre_404]

    lines = ["## New 404s", ""]
    if not regressions and not novel:
        lines.append("**Verdict: OK** — no 404s in the post-release window.")
        lines.append("")
        return "\n".join(lines) + "\n"

    verdict = "WARN" if regressions else "OK"
    lines.append(
        f"**Verdict: {verdict}** — {len(regressions)} regressions "
        f"(was 2xx in baseline), {len(novel)} novel (likely scanner noise)"
    )
    lines.append("")
    if regressions:
        lines.append("### Regressions (2xx in baseline → 404 post-release)")
        lines.append("")
        lines.append("| route | hits |")
        lines.append("|---|---|")
        for r, n in sorted(regressions, key=lambda x: -x[1])[:25]:
            lines.append(f"| `{r}` | {n} |")
        lines.append("")
    if novel:
        lines.append("### Novel 404 paths (context, not release regressions)")
        lines.append("")
        lines.append("```")
        for r, n in sorted(novel, key=lambda x: -x[1])[:15]:
            lines.append(f"  {n:>4}  {r}")
        if len(novel) > 15:
            lines.append(f"  ... and {len(novel) - 15} more")
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def analyze_blocked_delta(
    events: Iterable[AccessEvent],
    baseline: tuple[dt.datetime, dt.datetime],
    post: tuple[dt.datetime, dt.datetime],
) -> str:
    pre = 0
    po = 0
    for ev in events:
        if within(ev.ts, *baseline):
            pre += 1
        elif within(ev.ts, *post):
            po += 1
    base_rate = per_hour(pre, baseline[1] - baseline[0])
    post_rate = per_hour(po, post[1] - post[0])
    lines = ["## Blocked-access (444) delta", ""]
    if pre == 0 and po == 0:
        lines.append("_(no blocked-access traffic in either window)_")
        return "\n".join(lines) + "\n"
    verdict = "WARN" if post_rate > base_rate * 2 and po > 50 else "OK"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    lines.append(f"- Baseline: {pre} blocked requests ({base_rate:.1f}/h)")
    lines.append(f"- Post: {po} blocked requests ({post_rate:.1f}/h)")
    lines.append("")
    return "\n".join(lines)


def _quantiles(values: list[float], qs: tuple[float, ...]) -> list[float]:
    """Interpolated quantiles; returns one number per q in qs."""
    if not values:
        return [float("nan")] * len(qs)
    s = sorted(values)
    n = len(s)
    out = []
    for q in qs:
        if n == 1:
            out.append(s[0])
            continue
        idx = q * (n - 1)
        lo = int(idx)
        frac = idx - lo
        hi = min(lo + 1, n - 1)
        out.append(s[lo] + frac * (s[hi] - s[lo]))
    return out


def analyze_slow_routes(  # noqa: C901 — ported from ~/logs.analyze/analyze.py; refactor deferred
    events: Iterable[AccessEvent],
    baseline: tuple[dt.datetime, dt.datetime],
    post: tuple[dt.datetime, dt.datetime],
) -> str:
    """Per-route request_time p50/p95/p99 — skips gracefully if log_format
    doesn't include rt=."""
    pre: dict[str, list[float]] = collections.defaultdict(list)
    po: dict[str, list[float]] = collections.defaultdict(list)
    top_slow: list[tuple[float, AccessEvent]] = []
    any_rt = False
    for ev in events:
        if ev.rt is None:
            continue
        any_rt = True
        r = route_of(ev.path)
        if within(ev.ts, *baseline):
            pre[r].append(ev.rt)
        elif within(ev.ts, *post):
            po[r].append(ev.rt)
            top_slow.append((ev.rt, ev))

    lines = ["## Slow routes (request_time)", ""]
    if not any_rt:
        lines.append(
            "_(nginx log_format doesn't emit `rt=$request_time`; enable it "
            "per the ops doc to populate this section on the next release)_"
        )
        return "\n".join(lines) + "\n"

    regressions: list[tuple[str, float, float]] = []
    per_route: list[tuple[str, float, float, float, int, int]] = []
    for r in set(pre) | set(po):
        pre_p95 = _quantiles(pre[r], (0.95,))[0] if pre[r] else float("nan")
        post_p50, post_p95, post_p99 = _quantiles(po[r], (0.5, 0.95, 0.99))
        per_route.append((r, post_p50, post_p95, post_p99, len(pre[r]), len(po[r])))
        # post_p95 == post_p95 is an explicit NaN check (NaN != NaN).
        if (
            po[r]
            and post_p95 == post_p95
            and post_p95 > max(0.5, 2 * (pre_p95 if pre_p95 == pre_p95 else 0.0))
        ):
            regressions.append((r, pre_p95, post_p95))

    verdict = "OK" if not regressions else "WARN"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    if regressions:
        lines.append("### Regressions (post p95 > 2x baseline p95, floor 0.5s)")
        lines.append("")
        lines.append("| route | baseline p95 | post p95 |")
        lines.append("|---|---|---|")
        for r, a, b in sorted(regressions, key=lambda x: -x[2]):
            a_s = f"{a:.3f}s" if a == a else "n/a"
            lines.append(f"| `{r}` | {a_s} | {b:.3f}s |")
        lines.append("")
    lines.append("### Post-release latency per route (≥5 samples)")
    lines.append("")
    lines.append("| route | p50 | p95 | p99 | n(pre) | n(post) |")
    lines.append("|---|---|---|---|---|---|")
    per_route.sort(key=lambda x: -x[2] if x[2] == x[2] else 0.0)
    shown = 0
    for r, p50, p95, p99, np, npo in per_route:
        if npo < 5:
            continue
        lines.append(f"| `{r}` | {p50:.3f}s | {p95:.3f}s | {p99:.3f}s | {np} | {npo} |")
        shown += 1
        if shown >= 15:
            break
    if top_slow:
        lines.append("")
        lines.append("### Slowest individual post-release requests")
        lines.append("")
        lines.append("```")
        for rt, ev in sorted(top_slow, key=lambda x: -x[0])[:10]:
            lines.append(
                f"{rt:>7.3f}s  {ev.status}  {ev.method} {ev.path}  "
                f"{ev.ts.strftime('%m-%d %H:%M:%S')}"
            )
        lines.append("```")
    lines.append("")
    return "\n".join(lines)


def analyze_csp(  # noqa: C901 — ported from ~/logs.analyze/analyze.py; refactor deferred
    events: Iterable[CspEvent],
    baseline: tuple[dt.datetime, dt.datetime],
    post: tuple[dt.datetime, dt.datetime],
) -> str:
    pre: collections.Counter[tuple[str, str, str]] = collections.Counter()
    po: collections.Counter[tuple[str, str, str]] = collections.Counter()
    any_row = False

    for ev in events:
        any_row = True
        violated = (ev.violated or "-").split()[0]
        blocked_raw = str(ev.raw.get("blocked") or "-")
        try:
            bp = urlparse(blocked_raw)
            blocked_host = bp.scheme + "://" + bp.netloc if bp.netloc else blocked_raw
        except (ValueError, AttributeError):
            blocked_host = blocked_raw
        doc = ev.document_uri or "-"
        try:
            doc_path = urlparse(doc).path or "/"
        except (ValueError, AttributeError):
            doc_path = doc
        key = (violated, blocked_host, doc_path)
        if within(ev.ts, *baseline):
            pre[key] += 1
        elif within(ev.ts, *post):
            po[key] += 1

    lines = ["## CSP violations", ""]
    if not any_row:
        lines.append("_(no csp.log entries in either window)_")
        return "\n".join(lines) + "\n"

    new_keys = [k for k in po if pre[k] == 0]
    verdict = "WARN" if new_keys else "OK"
    lines.append(f"**Verdict: {verdict}**")
    lines.append("")
    lines.append(f"- Baseline: {sum(pre.values())} violations across {len(pre)} tuples")
    lines.append(f"- Post-release: {sum(po.values())} violations across {len(po)} tuples")
    lines.append("")
    if new_keys:
        lines.append("### New violation tuples (violated, blocked origin, document path)")
        lines.append("")
        lines.append("| count | violated | blocked | document |")
        lines.append("|---|---|---|---|")
        for k in sorted(new_keys, key=lambda k: -po[k])[:20]:
            v, b, d = k
            lines.append(f"| {po[k]} | `{v}` | `{b}` | `{d}` |")
        lines.append("")
    if pre and po:
        persistent = [(k, po[k], pre[k]) for k in po if pre[k] > 0]
        if persistent:
            lines.append("### Persistent tuples (both windows)")
            lines.append("")
            lines.append("| post | baseline | violated | blocked | document |")
            lines.append("|---|---|---|---|---|")
            for k, np_, pr in sorted(persistent, key=lambda x: -x[1])[:10]:
                v, b, d = k
                lines.append(f"| {np_} | {pr} | `{v}` | `{b}` | `{d}` |")
            lines.append("")
    return "\n".join(lines)


def analyze_gaps(
    access_events: Iterable[AccessEvent],
    csp_events: Iterable[CspEvent],
) -> str:
    """Note any data sources the operator hasn't enabled yet."""
    has_timed = False
    for ev in access_events:
        if ev.rt is not None:
            has_timed = True
            break
    has_csp = False
    for _ in csp_events:
        has_csp = True
        break

    lines = ["## Data sources available", ""]
    lines.append(f"- `rt=$request_time` in access logs: {'yes' if has_timed else 'no'}")
    lines.append(f"- CSP violation log (`csp.log`) present: {'yes' if has_csp else 'no'}")
    lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------


def run_postmortem(
    release: dt.datetime,
    baseline: tuple[dt.datetime, dt.datetime],
    post: tuple[dt.datetime, dt.datetime],
    tz: dt.tzinfo,
    access_log_glob: str = "/var/log/nginx/*access.log*",
    error_log_glob: str = "/var/log/nginx/*error.log*",
    blocked_log_glob: str = "/var/log/nginx/blocked-access.log*",
    csp_log_glob: str = "/home/pat/logs/csp.log*",
) -> str:
    """Pull events for the whole baseline+post span, then run each analyzer.

    Events are materialized as lists once and shared across analyzers
    that need overlapping windows (e.g., `analyze_http_status` +
    `analyze_new_404s` + `analyze_slow_routes` all consume access).
    """
    since = min(baseline[0], post[0]) - dt.timedelta(hours=12)

    access = list(iter_access_events(since=since, log_glob=access_log_glob))
    errors = list(iter_error_events(since=since, tz=tz, log_glob=error_log_glob))
    blocked = list(iter_blocked_events(since=since, log_glob=blocked_log_glob))
    units = list(iter_unit_events(since=since))
    csps = list(iter_csp_events(since=since, log_glob=csp_log_glob))

    git_commits = git_log_since(baseline[0])
    db_health = db_health_snapshot()
    deploy_listing = deploy_paths_listing()

    sections = [
        render_header(release, baseline, post, git_commits, db_health, deploy_listing),
        analyze_systemd_units(units, release, post[1]),
        analyze_http_status(access, baseline, post),
        analyze_error_clusters(errors, baseline, post),
        analyze_stale_deploy(errors, post, tz),
        analyze_new_404s(access, baseline, post),
        analyze_blocked_delta(blocked, baseline, post),
        analyze_slow_routes(access, baseline, post),
        analyze_csp(csps, baseline, post),
        analyze_gaps(access, csps),
    ]
    return "\n".join(sections)
