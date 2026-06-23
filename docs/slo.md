# Service-level objectives

Per `PLAN_production_discipline.md` Tier 4 / `docs/done/PLAN_outstanding_followups.md`
§6.2. These targets describe what "healthy" means for the live site
(`levels.wkcc.org`; `levels.mousebrains.com` and `levels-test.wkcc.org`
share the same nginx + DB). Single operator, hobby project — these are
internal thresholds for "should I look at this," not external SLAs.

When an SLO trends red, file it as an issue in the next operator review
(see `docs/operations.md` § Bus-factor partner) and decide whether the
target needs revision, a control needs strengthening, or the incident
was load-bearing for a one-off and the trend ignores it.

The same Better Stack monitors that drive SLO **A** also feed the
public status page at <https://status.mousebrains.com> — the audience
there is the bus-factor partner and any club member who wants to know
why the site is slow, not a contractual SLA reader. A green page +
red SLO trend is possible (e.g. data-freshness drift while the homepage
stays up); read the status page and this doc together.

## Targets

| # | SLO | Target | Measurement | Where the signal lives |
|---|---|---|---|---|
| **A** | Site availability | ≥ **99.5% / 30d** (~3.6 h/month error budget) | Better Stack uptime monitor on `https://levels.wkcc.org/`, 3-min interval, HTTP 2xx | Better Stack dashboard `Uptime / kayak` |
| **F** | Pipeline freshness | Global: newest observation across all sources ≤ **3 h** old. Per-source: every active gauge-linked fetch-backed source has ≥ 1 observation and none silent > **14 d** (`STALE_SOURCE_DAYS`); OGC-fetched USGS sources are checked for going silent too, but never-fed ones are exempt | `scripts/health-check.sh` exits non-zero on either; surfaced via `kayak-healthcheck.service` heartbeat. The global check fires every run; **per-source** stale alerts are rate-limited to ≤ 1 per `HEALTHCHECK_SOURCE_ALERT_DAYS` (default 7) per source, and a source can be muted via `HEALTHCHECK_MUTE_SOURCES` | `journalctl -u kayak-healthcheck` + healthchecks.io `kayak-healthcheck` check |
| **B** | Backup RPO | ≤ **1 h** confirmed by a successful hourly snapshot | `kayak-backup-hourly.service` (hourly `*:38`) pings healthchecks.io on success; backup files land at `/home/pat/backups/backup-<UTC>.db.gz` | healthchecks.io `kayak-backup-hourly` check + `ls -la /home/pat/backups/` |
| **D** | Build-time freshness | New static HTML written within **75 min** of the hourly pipeline tick (`kayak-pipeline.timer` runs at `*:12`, ~30 min budget for fetch + calc + build, +headroom) | `kayak-pipeline.service` heartbeat ping fires only after the build step exits 0 | healthchecks.io `kayak-pipeline` check + `stat /home/pat/public_html/Oregon.html` |
| **E** | Editor magic-link delivery | ≥ **95% / 30d** of magic-link emails reach the inbox within 60 s | `src/kayak/web/php/includes/mail.php` logs every send attempt; success measured by absence of msmtp error in `journalctl` and operator-noticed bounces | `journalctl -t magiclink` + `src/kayak/web/php/includes/mail.php` retry counters |

## How the budgets fire

* **Availability (A).** Better Stack alerts when the monitor's 3-min check
  fails twice in a row (≈6 min of downtime). One ~6-min hiccup costs ~3%
  of the monthly error budget. Two unrelated 6-min hiccups in a month
  still leave headroom; three or four says "stop tolerating one-off
  flakes."
* **Freshness (F).** `scripts/health-check.sh` returns non-zero when
  (a) the newest observation across **all** sources is older than 3 h —
  the pipeline stopped writing entirely — or (b) any active,
  gauge-linked, fetch-backed source has no observations at all or none
  newer than `STALE_SOURCE_DAYS` (default 14 days) — a single feed died
  while other sources kept the global timestamp fresh. OGC-fetched USGS
  sources (gauge-linked, no `fetch_url` row) get the same silent->fail
  window once they have produced data, but never-fed ones are exempt:
  they're speculative metadata additions awaiting upstream OGC
  coverage, and paging on them forever would train alert-blindness.
  There is no
  per-source cadence model (feeds update anywhere from every 15 min to
  a few times a day), so the per-source window is deliberately coarse:
  a dead-feed detector, not a lag detector. The **global** 3 h check fires
  every run, but **per-source** stale alerts are rate-limited: any one
  source pages at most once per `HEALTHCHECK_SOURCE_ALERT_DAYS` (default
  7), tracked in a small state file, and a known-dead source can be muted
  outright via `HEALTHCHECK_MUTE_SOURCES`. So healthchecks.io is **not** a
  continuously-red signal for a known-stale source — a single dead feed
  pages ~weekly (or never, if muted) and the heartbeat goes green between
  pages; the stale source stays visible on the green `OK:` line meanwhile.
  (A failure to persist that state is itself non-green, so a stale state
  can never silently hide a fresh outage.) Healthchecks.io fires when
  the heartbeat doesn't ping
  on time *or* the service exits non-zero (`ExecStartPost=-/usr/bin/curl`
  only runs when `ExecStart` succeeds). So "stale" and "unit crashed"
  both surface as the same alert.
* **Backup (B).** The hourly snapshot is the load-bearing RPO control.
  The Sunday weekly snapshot is the lower-volume retention copy plus
  the off-site upload trigger; it does not count toward the RPO target.
* **Build freshness (D).** The 75-min ceiling matches the operational
  note in `PLAN_production_discipline.md` Tier 1.2 — Better Stack pages
  "for hourly timers, fires within ~75 min" if the heartbeat goes
  missing. If real fetch times grow above the budget (e.g. USGS OGC
  going slow), tighten the pipeline DAG or split fetch into a second
  service before raising the SLO.
* **Magic-link (E).** A degraded msmtp / Gmail relay manifests as
  `mail.php` returning `false` from `send_magic_link()`. The 95% target
  reflects "best-effort, no second-channel fallback" — failures should
  be rare but aren't paged. Trending degradation triggers a rotation
  of the Gmail app-password or a switch to a transactional provider.

## What's intentionally not an SLO

* **Pipeline successes per day.** Per-source flake is normal and
  expected (network blips, upstream maintenance). The freshness SLO (F)
  catches the case that matters — observations stopped arriving — not
  the journal of which feed hiccuped on any given tick.
* **Editor uptime.** The editor surface (login / propose / review) is
  load-bearing for the maintainer workflow but not for public reads.
  Outage there is captured by SLO A (whole-site 200) plus the editor
  feature's own healthcheck path; it doesn't need a separate SLO.
* **Off-site backup latency.** The `kayak-backup-offsite.service`
  chained behind Sunday's weekly snapshot adds a second copy outside
  Hetzner. Failures already alert (Phase 1.4 notifier); a separate SLO
  here would just duplicate that signal.
* **Pipeline test-suite green-rate.** That belongs in CI dashboards,
  not the production-discipline SLO list.

## Re-evaluation

These targets are deliberately conservative — they let an obvious
regression surface without paging on every transient. Revisit when:

* A single month burns >50 % of the availability error budget (3.6 h ×
  0.5 ≈ **108 min downtime**) and the cause was not load-bearing.
* The freshness SLO trips twice in 30 days on the same source — either
  the source is unreliable enough to deserve a longer window (raise
  `STALE_SOURCE_DAYS` in the unit env) or its `fetch_url` deactivated,
  or the upstream API has changed and the parser needs attention.
* The hourly backup snapshot misses for any reason during normal
  operation. RPO ≤ 1 h is the headline number for "what can we lose if
  the disk dies right now"; it's not negotiable without a deliberate
  decision.
* A new SLO becomes obviously load-bearing (e.g. when a second
  maintainer joins, the editor surface's availability probably needs
  its own target).
