# Plan — Migrate `~/logs.analyze` into the repo as `levels analyze-logs`

> **Cross-check:** plan drafted 2026-05-15 against `main` at `b0e25f1`.
>
> **Iter log:**
> - iter 1 (2026-05-15): initial draft.
> - iter 2 (2026-05-15): 6 findings — (A) **CLI module names had
>   typos in iter 1.** Actual `src/kayak/cli/` listing (verified
>   `ls src/kayak/cli/`): `assign_huc.py`, `build.py`, `calc_rating.py`,
>   `calculator.py`, `decimate.py`, `delete_editor.py`,
>   `editor_retention.py`, `emit_config.py`, `export_editor.py`,
>   `fetch.py`, `fetch_usgs_ogc.py`, `init_db.py`, `logger.py` (shared
>   logger args, not a subcommand), `main.py` (entry point),
>   `merge.py`, `migrate.py`, `orphan_check.py`, `pipeline.py`,
>   `seed_maintainer.py`, `trace_reach.py`, `validate_config.py`.
>   Iter 1's `audit_gauges.py` / `build_cmd.py` / `merge_cmd.py` /
>   `analyze.py` claims were wrong. (B) **CLI entry point + wiring
>   shape located.** `pyproject.toml [project.scripts]` declares
>   `levels = "kayak.cli.main:main"`. `cli/main.py` imports each
>   subcommand module + calls `<module>.addArgs(subparsers)`. New
>   `analyze_logs.py` plugs into the same pattern: add an import
>   + one `analyze_logs.addArgs(subparsers)` line in
>   `cli/main.py`. (C) **Sub-sub-commands have no precedent in this
>   CLI** (all current subcommands are flat: `levels init-db`,
>   `levels build`, etc.). Argparse supports nesting cleanly via
>   `parser.add_subparsers(...)` though. For analyze-logs's three
>   modes with different flag sets, sub-sub-commands read better
>   than a `--mode` flag: `levels analyze-logs release
>   --baseline-hours 48` vs `levels analyze-logs --mode release
>   --baseline-hours 48`. Plan sticks with sub-sub-commands;
>   Decision §1 settled. (D) **`pat` has journalctl access without
>   sudo** (verified — `journalctl -u kayak-pipeline.service` runs
>   for `pat`, who's in the `adm` group per `groups pat`). The
>   `_log_sources.iter_unit_events` subprocess call works as-is.
>   (E) **nginx log path verified.** `/var/log/nginx/kayak-access.log*`
>   exists with 14+ rotated copies (current `.log` + `.log.1`
>   uncompressed + `.log.2.gz` through `.log.14.gz`). Single glob
>   `kayak-access.log*` matches both states — same pattern syncit
>   uses today. (F) **`src/kayak/analytics/` is a new top-level
>   subpackage** (joins `cli/`, `db/`, `huc/`, `parsers/`, `tracing/`,
>   `utils/`, `web/`). Not against convention; worth noting in
>   the commit message.
> - iter 3 (2026-05-15): 4 findings — (A) **Split `_log_sources.py`
>   into two modules.** Iter 1 lumped log iteration + DB queries +
>   git/stat into one file. Cleaner: `_log_sources.py` for log/journal
>   iteration only; `_release_context.py` for the metadata syncit
>   captures in `release/*` today (index.html mtime, `git log`
>   subprocess, DB-health SELECTs). Each ~100 lines. Easier to test
>   in isolation. (B) **Per-sub-command flag overlap.** All three
>   sub-commands share `--tz` (default America/Los_Angeles); release
>   adds `--baseline-hours / --window-hours / --release`; humans +
>   chunked share `--hours`; chunked alone adds `--bucket-hours`.
>   Use argparse's `parents=[...]` pattern to declare a shared parent
>   parser once and inherit common flags. (C) **Log-file iteration
>   order doesn't matter.** Today's `analyze.py:208 glob_rotated()`
>   does a lex sort that's "wrong" for rotated logs (`.log.10.gz`
>   sorts before `.log.2.gz`) but works in practice — each event has
>   its own timestamp, consumers filter via
>   `within(ts, lo, hi)`. The new `_log_sources.iter_access_events`
>   can do the same lex-sort glob without worrying about chronology.
>   (D) **Harvest dirs `~/logs.analyze/20260*` are operator-specific
>   data and stay untracked.** Tests use synthesized log strings
>   (Decision §4 recommendation already noted; cite the existing
>   harvest dirs only as a manual-smoke source post-implementation,
>   not as a tracked fixture).
> - iter 4 (2026-05-15): 4 findings — (A) **Multi-vhost log layout.**
>   Today's `analyze.py` reads `kayak-access.log*` (the legacy
>   single-vhost log). Live nginx writes to **three separate log
>   files**: `/var/log/nginx/kayak-access.log*` (mousebrains.com,
>   per legacy `conf/levels.nginx`), `levels-test.access.log*`
>   (levels-test.wkcc.org), `levels-wkcc.access.log*`
>   (levels.wkcc.org — currently low-traffic; becomes canonical
>   post-T+3 hostname cutover, per memory
>   `project_wkcc_hostname_transition`). Each is in `kayak_timed`
>   format (`rt=$request_time urt=$upstream_response_time` suffix
>   — `/etc/nginx/conf.d/kayak-log-format.conf`). New tool default:
>   accept a `--log-glob` flag (default `/var/log/nginx/*access.log*`
>   = all kayak vhosts). Post-cutover the default still works; pre-
>   cutover the analysis is still mostly accurate (mousebrains is
>   today's canonical traffic source). Cite the cutover memory in
>   the script docstring so a future operator knows why the glob is
>   wide. (B) **`kayak_timed` `loggable_uri` / `loggable_referer`.**
>   Format uses `$loggable_uri` and `$loggable_referer` (not raw
>   `$request_uri` / `$http_referer`) — confirmed in
>   `/etc/nginx/conf.d/kayak-log-format.conf`. These are
>   query-string-scrubbed variants nginx sets via a `map` directive
>   (verify in the same conf file). The existing access-log parser
>   regex `analyze.py:49-58 _COMBINED_RE` doesn't care — it parses
>   the request field as opaque text. No port change needed.
>   (C) **Subprocess (not systemd-python) for journalctl.** Use
>   `subprocess.run(["journalctl", ...], text=True, capture_output=True,
>   check=False)`. Matches syncit's current approach; avoids adding
>   `systemd-python` as a build-time dependency. Errors (journal
>   read denied, journalctl missing) surface as empty output + a
>   logged warning rather than a crash. (D) **`reference_logs_analyze`
>   memory update is non-negotiable.** Today's body claims the tools
>   are "untracked" and "uses index.html.mtime line post-migration."
>   Post-migration: drop the "untracked" qualifier, point at
>   `src/kayak/cli/analyze_logs.py` + `src/kayak/analytics/*`, note
>   that syncit is gone (no harvest step), and update the CLI usage
>   to `levels analyze-logs <subcommand>`.
> - iter 5 (2026-05-15, stopping): 2 findings — (A) **CSP log
>   format verified.** `php/csp-report.php` writes JSON-per-line to
>   `Config::str('csp_log_path')` (default `~/logs/csp.log`).
>   Fields: `ts, ip, ua, document_uri, referrer, violated, ...`
>   (sample confirmed against `~/logs/csp.log`). Rotated weekly by
>   `/etc/logrotate.d/kayak-csp`. `iter_csp_events` parses each
>   line with `json.loads`; ignore lines that fail to parse (skip
>   silently — corrupt-line fail-soft is the right default for an
>   analytics tool). (B) **Convergence pattern.** Findings per iter:
>   draft → 6 → 4 → 4 → 2. Decreasing returns; remaining items are
>   manual-smoke verifications post-implementation, not plan-level
>   gaps. Stopping.
>
> Dates absolute. References `file:line` against current `main` (or
> `~/logs.analyze/<file>:line` for the in-flight tools that aren't
> tracked yet).

## Why

Per `PLAN_production_discipline.md` Phase 2.5. Today the operator
analytics tools live in `~/logs.analyze/` (untracked) on the live
host:

- `analyze.py` (991 lines) — release post-mortem. Compares a
  baseline window (default 48h pre-release) vs a post-release
  window. ~9 sub-analyses: systemd units, HTTP status mix, error
  clusters, stale-deploy detection, new 404s, blocked-traffic
  delta, slow routes, CSP violations, observation gaps.
- `chunked_humans.py` (176 lines) — 2-hour buckets of
  human-vs-bot traffic across the last 48h.
- `human_users.py` (182 lines) — distinct-human visitor count.
- `syncit` (49 lines) — bash harvester: rsync /var/log/nginx,
  /var/log/php8.4-fpm.log*, ~/logs/csp.log*, plus journalctl
  snapshots, git log, deploy paths, DB health, into a dated
  directory `~/logs.analyze/YYYYMMDD/`.

Run cadence today: roughly twice a week, on-demand, by the
operator at the shell. No automated trigger. Output is Markdown
on stdout for human reading.

**The plan's original Phase 2.5 description** (PLAN_production_discipline.md
lines 164-168) imagined a daily anomaly-detector emailing reports +
ntfy-ing on critical lines. That mismatches the actual tools, which
are interactive on-demand analytics. **User decision (chat
2026-05-15):** re-scope 2.5 as "migrate the existing tools into the
repo as `levels analyze-logs` CLI sub-commands; drop syncit (read
/var/log + journalctl directly)." Daily-anomaly-detection becomes a
separate future phase, not bundled here.

## Scope inventory (verified against current `main` + `~/logs.analyze`)

**Existing tools** (`~/logs.analyze/`, untracked, ~1349 lines total):

| File | Lines | Role | Input |
|---|---|---|---|
| `analyze.py` | 991 | Release post-mortem comparing pre/post windows | Harvest dir with parsed log files |
| `chunked_humans.py` | 176 | 2h-bucketed human-vs-bot table | Latest harvest dir |
| `human_users.py` | 182 | Distinct-human count, last 48h | Latest harvest dir |
| `syncit` | 49 | Harvester: rsync logs + journalctl + git + DB-health into a dated dir | Reads /var/log + ~/logs + journalctl + repo |
| Dated output dirs `20260501/`-`20260511/` | — | Harvested input + (no reports — analyze.py emits to stdout) | — |

**`analyze.py` internal surface (`~/logs.analyze/analyze.py`):**

Parsers + namedtuples (lines 49-98):
- `AccessEvent`, `ErrorEvent`, `UnitEvent`
- `parse_access()` — nginx combined format with optional rt/urt suffixes
- `parse_error()` — nginx error log multi-line builder
- `parse_journal()` — `journalctl -o short-iso` lines

Sub-analyses (one function each, names speak for themselves):
- `analyze_systemd_units` (line 368)
- `analyze_http_status` (line 429)
- `analyze_error_clusters` (line 499)
- `analyze_stale_deploy` (line 555)
- `analyze_new_404s` (line 600)
- `analyze_blocked_delta` (line 662)
- `analyze_slow_routes` (line 715)
- `analyze_csp` (line 799)
- `analyze_gaps` (line 895)

Each takes the parsed events + the (release_at, baseline_lo, post_hi)
time markers and emits one Markdown section.

`main(argv)` argparse (line 931):
- `harvest` (positional) — dir path
- `--release ISO8601` (default: infer from deploy-paths.txt symlink mtime)
- `--baseline-hours` (default 48)
- `--window-hours` (default 0 = "until now")
- `--tz` (default America/Los_Angeles)

**`chunked_humans.py` / `human_users.py`:** both auto-pick the latest
harvest dir under `~/logs.analyze/`. Bot-filter regexes + Pat's home
IP + Uptrends-cluster detection are duplicated between them — port
once into a shared helper.

**Existing kayak CLI structure** (`src/kayak/cli/`):

- Each subcommand exposes `addArgs(subparsers)` and sets `args.func`
  as the handler (`docs/CLAUDE.md` § CLI Pattern).
- Module list today: `audit_gauges.py`, `build_cmd.py`, `calculator.py`,
  `decimate.py`, `fetch.py`, `init_db.py`, `logger.py`, `merge_cmd.py`,
  `migrate.py`, `orphan_check.py`, `pipeline.py`, `rating.py`,
  `seed_maintainer.py`, `trace.py` (verify count with `ls src/kayak/cli/`).
- Wired into the entry point at `src/kayak/__main__.py` or
  `src/kayak/cli/__init__.py` (TBD — confirm in iter 2).

**Live host access for direct reads (replacing syncit):**

- `/var/log/nginx/*.log*` — `pat` is in `adm` group (verify
  `groups pat`). Logrotate-compressed `.gz` files included.
- `/var/log/php8.4-fpm.log*` — same group.
- `~/logs/csp.log*` — pat-owned via ACL, already accessible.
- `journalctl --since=...` — works for `pat` without sudo (the `adm`
  group plus systemd's user-bus access).
- Git log — pat owns the repo.
- DB health — `sqlite3 ~/DB/kayak.db` works (pat owns DB dir).
- Deploy paths — `stat /home/pat/public_html/index.html` works.

**Live constraints**:

- Ruff `E W F I UP B SIM RUF` (pyproject.toml).
- Python 3.13 target.
- mypy on `src/kayak/` — type hints required throughout.
- 100-char line length.
- Pytest must work without disk I/O — but `analyze-logs` is operator-
  only, not part of the pipeline; tests can use tmp fixtures.

## Approach

### CLI shape

Add `src/kayak/cli/analyze_logs.py` with a single top-level
`analyze-logs` command + three sub-commands:

```
levels analyze-logs release [--since ISO] [--baseline-hours N]
                            [--window-hours N] [--tz ZONE]
                            [--release ISO]
levels analyze-logs humans  [--hours N] [--tz ZONE]
levels analyze-logs chunked [--hours N] [--bucket-hours N] [--tz ZONE]
```

All three emit Markdown to stdout — matches today's UX.
`--release` defaults to `stat index.html` mtime (the build's
authoritative tail, same convention status.json uses).

**Shared module** `src/kayak/analytics/_log_sources.py` (new) wraps
the log-read primitives:

- `iter_access_events(since: datetime) -> Iterator[AccessEvent]`
  — reads `/var/log/nginx/kayak-access.log*` (handles `.gz`),
  yields combined-format events.
- `iter_error_events(since) -> Iterator[ErrorEvent]` — same for
  `kayak-error.log*`.
- `iter_blocked_events(since)` — `blocked-access.log*`.
- `iter_unit_events(since, units: list[str]) -> Iterator[UnitEvent]`
  — wraps `journalctl --since=... -u 'kayak-*' -o short-iso`.
- `iter_csp_events(since)` — `~/logs/csp.log*`.
- `db_health_snapshot() -> dict[str, int|str]` — opens kayak.db,
  runs the same select-count queries syncit emits today.
- `git_log_since(since) -> list[str]` — subprocess wrapper.

The 9 `analyze_*` functions in the existing `analyze.py` move into
`src/kayak/analytics/release_postmortem.py`, refactored to read
from `_log_sources` instead of a harvest dir. Their logic stays
mostly verbatim (well-tested by months of operator use).

`chunked_humans.py` + `human_users.py` move into
`src/kayak/analytics/humans.py`, sharing the
bot/synthetic-filter regexes via a single `is_synthetic_ua(ua)` helper.

### Migration strategy

1. **Port the log-source primitives first.** `_log_sources.py` reads
   directly from /var/log + journalctl + DB; no harvest dir. Each
   iterator function is small and testable in isolation.
2. **Port analyze_* functions, one at a time.** Each one's
   pre/post diff math is independent of the others — easy to port
   incrementally.
3. **Port the humans tools.** Smaller — 176 + 182 lines minus
   ~50 lines of duplicated filter helpers.
4. **Wire the CLI.** Single module `src/kayak/cli/analyze_logs.py`
   with three sub-commands. Register in the CLI entry point.
5. **Delete `~/logs.analyze/`?** Decision §3 below.

### Out-of-scope (deferred to a future phase)

- **Daily anomaly-detection timer + email + ntfy push.** The plan's
  original 2.5 description; mismatched these tools. **Per user
  decision 2026-05-15: revisit at T+30** (~mid-June 2026, after the
  WKCC hostname cutover stabilizes). A future
  `kayak-analyze-logs.timer` is reasonable but needs its own
  thresholds + reasoning — separate plan with its own iter loop.
- Persisting `fetch_event` table for the internal dashboard's
  recent-fetch-errors widget (Tier 2.4). Different shape (per-
  source error history, not per-release diff).
- Replacing analyze.py's stdout markdown with JSON / HTML.
- Migrating the dated `~/logs.analyze/20260*` directories — they're
  inputs, not outputs, and they'll naturally age out once syncit is
  gone.

## Files affected

- **New:**
  - `src/kayak/analytics/__init__.py`
  - `src/kayak/analytics/_log_sources.py` — primitives (~200 lines)
  - `src/kayak/analytics/release_postmortem.py` — ported `analyze_*`
    functions + Markdown emitters (~700 lines, slimmed from 991
    by removing harvest-dir plumbing).
  - `src/kayak/analytics/humans.py` — ported humans tools (~250
    lines, slimmed from 358 by deduplicating filter helpers).
  - `src/kayak/cli/analyze_logs.py` — CLI wrapper (~80 lines).
  - `tests/test_analytics_log_sources.py` — happy-path tests with
    fixture log lines.
  - `tests/test_analytics_release_postmortem.py` — at least one
    test per sub-analysis using a synthetic event stream.

- **Modified:**
  - `src/kayak/cli/__init__.py` (or wherever subcommands are
    registered) — wire `analyze-logs`.
  - `docs/PLAN_production_discipline.md` Status banner — mark 2.5 done.
  - `docs/operations.md` — add a row referencing the new command
    under Health endpoints / monitoring map (or a new "Operator
    analytics" section).

- **Memory:**
  - Update `~/.claude/projects/-home-pat-kayak/memory/reference_logs_analyze.md`:
    drop the "untracked" qualifier, point at the new repo location,
    note the syncit removal.

- **Deleted (after first successful run from the new location):**
  - `~/logs.analyze/analyze.py`, `chunked_humans.py`, `human_users.py`,
    `syncit`. The dated input dirs (`~/logs.analyze/20260*`) stay
    until they age out.

## Edge cases

- **Logrotate compressed inputs.** `/var/log/nginx/*.log.N.gz`
  needs `gzip.open` — port `_open_text()` helper from analyze.py:102.
- **TZ.** Today's tools assume `America/Los_Angeles`. Keep that
  default; expose `--tz` per existing analyze.py.
- **No release marker.** If `index.html` doesn't exist yet (fresh
  install), `analyze-logs release` errors out — same as today's
  "could not infer release time from deploy-paths.txt".
- **Empty journals.** `journalctl --since=X` returns 0 lines.
  Iterators must yield 0 events, not crash.
- **CSP log absent.** `~/logs/csp.log` may not exist on dev
  machines. Syncit already silently skips; mirror via try/except.
- **Bot/synthetic filter drift.** UA regexes need maintenance as
  scanners rotate UAs. Bundle them in one helper for easy editing.

## Testing approach

- **Unit tests for `_log_sources`:** synthetic log lines (combined
  format, error multi-line, journal short-iso) passed through the
  iterators; assert event-tuple shape.
- **Unit tests for sub-analyses:** synthetic event stream input,
  assert Markdown output contains expected counts / cluster names.
- **No live host hits** in tests — pytest stays in-memory.
- **Manual smoke** post-implementation: run each sub-command on
  the live host with output compared to the most recent `~/logs.analyze`
  run by hand. Spot-check that count totals match.

## Risk

Medium. analyze.py has been months-debugged for the operator's
specific log volume / UA mix. Porting risks:

- **Filter regex drift** — moving the bot/synthetic UA list might
  miss a regex variant. Mitigation: copy the list verbatim; review
  test failures against a known harvest dir.
- **Harvest-dir → direct-read assumptions** — analyze.py's
  `glob_rotated()` (line 208) assumes `kayak-access.log*` files in
  the harvest dir. The direct-read version globs `/var/log/nginx/`.
  Both work but the test setup is different.
- **990-line port produces a 700-line rewrite** — line-count
  shrinkage is the goal (drop harvest plumbing) but it's easy to
  drop a feature accidentally. Mitigation: per-sub-analysis ports
  + tests, not one big bang.

## Decisions (settled across iter 1-5, pending user approval)

1. **CLI shape: sub-sub-commands.** `levels analyze-logs release`,
   `levels analyze-logs humans`, `levels analyze-logs chunked`.
   Each sub-sub-command has its own flag set; argparse's
   `parents=[...]` shares the cross-cutting `--tz` /
   `--log-glob` flags. First sub-sub-command precedent in the
   `levels` CLI; aligns with the natural verb structure of the
   tools.
2. **Module layout: separate `_log_sources` + `_release_context`
   + sub-command business modules.** `analytics/_log_sources.py`
   (log + journal iteration), `analytics/_release_context.py`
   (index.html mtime + git + DB-health), `analytics/release_postmortem.py`
   (the 9 `analyze_*` functions ported from analyze.py), and
   `analytics/humans.py` (the dedup'd chunked + distinct-human
   tools). Thin `cli/analyze_logs.py` dispatches.
3. **Delete `~/logs.analyze/*.py` after one operator-week of
   parallel use.** Migration commit keeps both available; a
   follow-up commit (~1 week later) deletes the in-`$HOME` copies.
   Dated directories `~/logs.analyze/20260*` age out passively
   via logrotate-style operator cleanup. **syncit is deleted
   immediately** — the new tool reads /var/log directly, no
   harvest step.
4. **Test fixtures: hard-coded log strings, no gzip fixture.**
   Cheap, readable, no extra files. The `.gz`-handling code path
   gets a unit test using `gzip.compress(bytes)` in-memory rather
   than a tracked binary fixture.
5. **Migration commit shape.** Single commit covers: new
   `src/kayak/analytics/*`, new `src/kayak/cli/analyze_logs.py`,
   wiring in `cli/main.py`, tests, plan-banner refresh in
   `PLAN_production_discipline.md`, operations.md addition, and
   memory `reference_logs_analyze.md` update. The `~/logs.analyze/*.py`
   deletions ride in a separate commit one operator-week later
   (per Decision §3).
6. **Default `--log-glob`:** `/var/log/nginx/*access.log*` so
   all three vhosts' logs (kayak-, levels-test., levels-wkcc.)
   feed into a single analysis. Survives the post-T+3 hostname
   cutover without operator action.

All six decisions are reversible — the CLI shape can split into
multiple top-level subcommands later if sub-sub-commands prove
awkward; the module split can merge if it feels over-decomposed
in practice.
