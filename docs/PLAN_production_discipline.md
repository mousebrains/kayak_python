# Plan — Production discipline for kayak

**Status:** Pre-v1.0.0 scope **landed 2026-05-15**. Tier 1 fully
landed (heartbeats + push notifications, plus `kayak-fail-test.service`
drill target). Tier 2 fully landed — 2.1 (`/status.json`), 2.2
(synthetic content check), 2.3 (`status.mousebrains.com` Better
Stack hosted page), 2.4 (internal dashboard at
`levels.wkcc.org/_internal/`), 2.5 (logs.analyze migration →
`levels analyze-logs`). Tier 3 partial — 3.1 (`scripts/deploy.sh`)
and 3.4 (rollback runbook) done; **3.2/3.3 (GHA staging+prod deploy)
and 3.5 (deploy drill) deferred to ~T+30** per chat decision
2026-05-15 (paired with `PLAN_three_instance_layout.md`'s
three-instance work, which is the natural home for staged deploys).
Tier 4 mostly done — 4.1/4.2/4.3/4.5 in `docs/operations.md` +
`docs/slo.md`; **4.4 (recovery drill log) deferred to ~T+30**
(pairs with the drill scenarios in 3.5). The daily-anomaly variant
of 2.5 is also a T+30 follow-up. Nothing left to ship before
v1.0.0 — close out scheduled after the T+30 deferrals land.

Phase 2/3/4 work overlaps with the audit follow-up plan
(`PLAN_pre_release_followup.md` Tiers 1-3); this plan
is the source of truth for deploy.sh / runbook / SLO / drill items,
while the audit plan covers cert monitor + test/CI maturity + architecture.

**Note on timer counts in the iter log:** historical "8 services"
counts refer to the pre-2026-05-14 state. As of 2026-05-14 the live
host runs 12 timers (added kayak-backup-hourly, kayak-cert-expiry,
kayak-cert-renewal-test, kayak-config-drift, kayak-metadata-snapshot;
renamed kayak-backup → kayak-backup-weekly). See `deploy/SETUP.md`
§timer schedule for the authoritative current list.

> **Cross-check:** plan drafted 2026-05-11 against the live host (`levels.mousebrains.com`, single Hetzner VPS, sole operator). Iterated 2026-05-12 against `main` at `ffbb387` plus the live `systemctl` state. A subsequent session should re-run the read-only commands in **§Reproduce** to confirm the current-state findings before any tier starts.
>
> **Iter log:**
> - iter 1 (2026-05-12): 14 findings — `§Reproduce` reveals Tier 1 isn't from scratch. `kayak-notify-failure@.service` already exists and is wired to all 7 `kayak-*` services via `OnFailure=`; emails pat.kayak@gmail.com via msmtp and logs to syslog. Plan reframes Phase 1.4 from "add new template" to "extend existing notifier with parallel ntfy channel." `kayak-heartbeat.timer` also already exists as a weekly positive-signal email — `systemd/kayak-heartbeat.sh:1-5` explicitly inverts "only hear on failure"; Tier 1.5 drill must verify the chain after Phase 1.4 touches the notifier. `kayak-healthcheck.service` already runs hourly `scripts/health-check.sh` (exit codes 0/1/2 designed for external uptime checkers) — Tier 1.2's Better Stack monitor and Tier 2.1's `/status.json` can reuse it. Concrete timer count is 7 with named cadences: `kayak-pipeline` (hourly *:12), `kayak-healthcheck` (hourly *:45), `kayak-decimate` (02:32 daily), `kayak-editor-retention` (03:42 daily), `kayak-backup` (Sun 03:15), `kayak-audit-gauges` (2nd+17th 03:00), `kayak-heartbeat` (Sun 06:00). Other corrections: repo path is `systemd/`, not `deploy/systemd/` (Phase 1.3/1.4 references); DNS is Cloudflare (relevant when Tier 2.3 adds `status.mousebrains.com`); `kayak-backup-offsite.service` is in repo but not installed (drift — Tier 1 should decide install-or-remove); ntfy.sh topics are public (anyone with name can subscribe — explicit security note); `§Reproduce` timer-enumeration loop uses fragile `awk '{print $NF}'`; healthchecks.io free tier (20 checks) easily covers the 7 timers.
> - iter 2 (2026-05-12): 7 findings — Phase 1.3 `ExecStartPost` curl that fails (network glitch) cascades to OnFailure and triggers a false alert. Prefix with `-` (`ExecStartPost=-/usr/bin/curl ...`) to mark the step ignorable per systemd convention. Phase 1.4 ntfy curl lacks an `NTFY_TOPIC`-unset guard — if Phase 1.4 lands before Phase 1.1 or `NTFY_TOPIC` is later rotated out, the curl POSTs to `ntfy.sh/` (invalid). Added `[ -n "${NTFY_TOPIC:-}" ] &&` guard. Phase 1.4 drill referenced placeholder `kayak-test.service` which doesn't exist; reworded to target an actual unit name (notifier doesn't check unit state, only uses `%i`). Phase 1.2 was vague about Better Stack free-tier specifics; baked in "10 monitors, 3-min interval, 1-month retention" with the verify-at-signup caveat. Tier 1 verification gate's "killed timer fires within one cadence window" hides that cadences range from hourly to bimonthly; tightened to "for hourly timers, fires within ~75 min." Added healthchecks.io check-naming convention (name = service name, e.g. `kayak-pipeline.service`) so the dashboard reads naturally. Cross-plan note: Tier 5.A (auth.php split) is load-bearing for the editor feature — Tier 1.2's Better Stack monitor catches an outright outage but won't surface a broken editor flow; Tier 2.2's synthetic content check on Oregon.html catches a build break, not a propose/review break. Documented as a known gap.
> - iter 3 (2026-05-12): 7 findings — iter 1 misread `kayak-backup-offsite.service` state. It IS installed at `/etc/systemd/system/kayak-backup-offsite.service` — triggered by `kayak-backup.service`'s `OnSuccess=kayak-backup-offsite.service`, not by its own timer (intentionally — chained, not scheduled). Iter 1's `systemctl list-timers` audit missed `OnSuccess=`-chained services entirely. Plan-table grows from 7 to 8 rows (kayak-backup-offsite added with chained cadence); §Reproduce gets a chained-service check. Other corrections: "Decisions baked in" still said `A record` and "whatever registrar holds mousebrains.com" — replaced with Phase 2.3's now-known `CNAME` + Cloudflare facts. §Reproduce's last comment "Confirm no existing kayak-failure-notify@.service" was inverted by the iter 1 finding (Phase 1.4 now expects it); reframed. Tier 4 Phase 4.2 runbook entries have stale unit names (`kayak-build`, `kayak-fetch.timer`) from the pre-build-split era; corrected to `kayak-pipeline*` (build is part of the pipeline now). Verification gate's "7 services" count bumped to 8.
> - iter 4 (2026-05-12): 5 findings — Phase 1.5 scenario 2 says "modify a kayak service to `exit 1` for one run" — that mutates a live service file. Replaced with creating a permanent `systemd/kayak-fail-test.service` (`ExecStart=/bin/false` + `OnFailure=`) as the canonical drill target; reusable for future Tier work. Phase 1.3 didn't explain how to map systemd OnCalendar to healthchecks.io's schedule expectation — added: cron expression matches `OnCalendar` 1:1, simpler "every N min" form matches the two hourly units. Added Tier 1 operational note about `levels pipeline` exit semantics: returns 0 even when some sources fail to fetch (by design — different layers catch different failure modes); `kayak-healthcheck` catches stale data, `analyze_logs.py` (Tier 2.5) catches per-source. Added operational note about the `kayak-backup` / `kayak-backup-offsite` chain: a `kayak-backup` failure prevents the chained offsite from running, so the operator gets BOTH a notifier alert (backup failed) AND a future healthchecks "no ping in N days" alert (offsite stale) — expected, not a duplicate. Added Risks entry: notifier itself can silently fail (msmtp broken, curl times out); pipe-to-logger in Phase 1.4 ensures syslog always sees the attempt. Verified `~/.config/kayak/.env` is `chmod 600` (no Phase 1.1 step needed) and `docs/offsite-backup.md` + `docs/db_sync.md` both exist (Tier 4 cross-refs valid). iOS push via ntfy.sh public service traverses an ntfy-operated Apple-push intermediary; documented in Phase 1.1 as a footnote.
> - iter 5 (2026-05-12): 4 findings — Phase 4.2 runbook "DB corruption: restore from `~/backups/kayak.db.<date>`" has the wrong path AND filename format. Actual: backups live at `/home/pat/kayak/backups/backup-YYYYMMDDTHHMMSSZ.db.gz` (gzipped, inside the repo; see `systemd/kayak-backup.sh:BACKUP_DIR`). Plan also said `~/backups/` in §Phase 4.1's "where backups live" — corrected. Tier 1 verification gate said "out of scope to drill all seven" — stale after iter 3 bumped the count to 8; corrected. Phase 4.3 SLO "Build cadence ≥1 successful build/h" doesn't say WHICH healthchecks.io check measures it; pinned to `kayak-pipeline.service`'s heartbeat (the build runs as the last stage of the pipeline since the build.py split). Convergence: 14 → 7 → 7 → 5 → 4 findings.
> - iter 6 (2026-05-12): 2 findings — §Reproduce's `ls -la ~/backups/` was iter 5's other stale-path residue (the directory doesn't exist); corrected to `/home/pat/kayak/backups/`. §Reproduce's `# Confirm registrar so status.mousebrains.com can be added` was soft-confirmed by iter 1's check; reframed as "re-confirm" with the 2026-05-12 result noted in-line. Convergence: 14 → 7 → 7 → 5 → 4 → 2 findings. Stopping; remaining items are aesthetic.
> - iter 7 (2026-05-12, post-stopping feedback fixup): 2 findings raised by an external review of the converged plan. (a) Cross-plan note at the bottom of Tier 1 was framed as if `auth.php` split were still in-flight, but it landed during the iter loop as `c161ae5` (Tier 5 complete per `f4968c3`). Reframed to keep the substantive gap (uptime monitor doesn't surface broken editor flow) without the "in-flight risk" framing. (b) Phase 1.5's `kayak-fail-test.service` will be the 9th installed kayak-* unit, but Tier 1 verification gate counts "all 8" service units that ping healthchecks.io. Added a one-sentence note in Phase 1.5 that the drill unit is intentionally outside the heartbeat set (always fails by design; no `ExecStartPost`).
>
> Dates absolute. References `file:line` against current `main`.

## Why

Today: when the site goes down or a scheduled job stops running, no one knows until you visit the site or run `~/logs.analyze` manually. Deploys mean sshing in, copying files, occasionally editing a config in place. Recovery procedures live in your head.

Goal: detect failures within minutes (not "next time you check"); make recovery procedures discoverable; promote deploys through CI; define an honest SLO. End state is **operable by someone other than you for at least a short period** — a friend can find the runbook and recover from common failures while you're unavailable.

## Constraints (stated by the user)

- **Don't clutter `levels-test.wkcc.org` or `levels.wkcc.org`.** Public-facing surfaces live on `levels.mousebrains.com` (vhost) or a new DNS under `mousebrains.com` (e.g. `status.mousebrains.com`).
- **Mixed hosting model:** SaaS for the must-be-external pieces (uptime, heartbeats, paging), self-host for the nice-to-have pieces (internal dashboard). SaaS sitting outside your infra is what makes "detect host outage" work at all.
- **Single operator, no SLA.** Notification model is email + phone push (ntfy.sh or Pushover); no real-paging tier today.
- **Phased.** Four tiers, review gate between tiers. Same workflow as `PLAN_build_split.md`.

## Decisions baked in

- **Heartbeats:** [healthchecks.io](https://healthchecks.io). Free tier should cover this project's timer count; verify limits at signup.
- **Uptime + status page:** [Better Stack](https://betterstack.com) (formerly BetterUptime). The free tier includes a hosted status page — bundling Tier 1 monitoring + Tier 2.3 in the same SaaS. Alternative: UptimeRobot, which historically allows more monitors but no bundled status page. Verify both products' current free-tier terms at signup before committing.
- **Push:** [ntfy.sh](https://ntfy.sh) public service. Self-hostable later if needed. Pushover ($5 one-time per platform) is the paid alternative.
- **Internal dashboard:** Hand-rolled HTML page reading `kayak.db` directly. No Grafana stack at this scale — a 100-line PHP/Python page beats a metrics-pipeline for the metrics we actually need.
- **Public status page DNS:** `status.mousebrains.com` (new CNAME under Cloudflare; `mousebrains.com` is at `liv.ns.cloudflare.com` / `dale.ns.cloudflare.com`).
- **Internal dashboard DNS:** `levels.wkcc.org/_internal/` (vhost subpath, maintainer-auth via the existing `editor_session` cookie, `noindex`; `levels-test.wkcc.org` has since been 301-redirected wholesale to the canonical host, 2026-05-19). No new DNS, no new credential. Lives on the wkcc hosts because `SITE_URL=levels.wkcc.org` drives the magic-link round-trip — anchoring the dashboard to a different host would break login.
- **Per [feedback_no_sudo]:** all `/etc/` edits (systemd units, nginx vhosts, certbot) get prepared as diffs that you apply. Per [feedback_systemd_in_tree_copy]: every `/etc/systemd/system/kayak-*` patch also goes into the repo's installed location at the same time.

## Target shape

| Tier | Delivers | SaaS | Self-host |
|---|---|---|---|
| 1 — Crash detection | Email + push within minutes of: site down, missed timer, unit failure | Better Stack (uptime), healthchecks.io (heartbeats), ntfy (push) | systemd `OnFailure=` template + per-timer `ExecStartPost` curl |
| 2 — Status visibility | Public status page; internal dashboard; queryable `/status.json` | Better Stack hosted status page | `/status.json` endpoint; internal dashboard at `levels.wkcc.org/_internal/`; `logs.analyze` migrated into repo |
| 3 — Deploy automation | Push to main → tests pass → auto-deploy to staging; tagged release → manual approval → prod | GitHub Actions | `scripts/deploy.sh`; rollback procedure |
| 4 — Runbook + SLO | `docs/operations.md`; defined SLO; recovery drill done with someone else at the keyboard | — | Runbook; `docs/slo.md`; quarterly drill log |

## Migration tiers

Each tier is several phases; **review gate between tiers**, not between phases.

### Tier 1 — Crash detection

**Goal:** Know within minutes when the site is down or a scheduled job stopped running.

1. **Phase 1.1 — Notification routes.** Create ntfy.sh topic with a high-entropy name (e.g. `kayak-$(openssl rand -hex 12)`). **Security note:** ntfy.sh's public service has no auth — the topic name *is* the credential. Anyone who learns it can read alerts and inject fake ones. Keep it out of public commits, logs, and screenshots. Test with `curl -d "test" ntfy.sh/<topic>`. Save as `NTFY_TOPIC` in `~/.config/kayak/.env` (already `chmod 600`, verified 2026-05-12 — no extra ACL step needed). Subscribe on phone (ntfy app on iOS or Android). Verify push within 30s. If the topic ever leaks (e.g. accidentally posted in a chat or commit), rotate to a new one and update both the env file and any phone subscription. **iOS footnote:** iOS notifications via the public ntfy.sh service traverse an ntfy-operated Apple-push intermediary (iOS push requires Apple-approved servers). Reliable for this scale; self-host requires a paid Apple developer cert if you need to bypass the intermediary.
2. **Phase 1.2 — External uptime monitor.** Sign up for Better Stack free tier (verify at signup: ~10 monitors, ~3-min check interval, 1-month retention). Create one monitor for `https://levels.mousebrains.com` (HEAD or GET, expect 200, 3-min interval). Add notification channels: email + ntfy webhook (Better Stack supports custom webhook destinations — point at `ntfy.sh/$NTFY_TOPIC`). Test by pausing nginx briefly on a quiet evening; confirm alert + recovery.
3. **Phase 1.3 — Heartbeat per systemd service.** healthchecks.io account; create one check per service unit. **Current count: 14** (verified 2026-05-15 — see §Reproduce; `deploy/SETUP.md` §timer schedule is the authoritative listing):

   | Service | Cadence | Notes |
   |---|---|---|
   | `kayak-pipeline.service`         | hourly `*:12`     | fetch + build (most important) |
   | `kayak-backup-hourly.service`    | hourly `*:38`     | local hourly DB snapshot |
   | `kayak-healthcheck.service`      | hourly `*:45`     | runs `scripts/health-check.sh` (data-freshness sentinel) |
   | `kayak-decimate.service`         | daily `02:32`     | thins old observations |
   | `kayak-metadata-snapshot.service` | daily `04:30`    | commits metadata-CSV drift to git |
   | `kayak-cert-expiry.service`      | daily `06:30`     | TLS cert-days-remaining probe |
   | `kayak-editor-retention.service` | daily `03:42`     | editor-row retention sweep |
   | `kayak-cert-renewal-test.service` | weekly Mon 04:15 | `certbot renew --dry-run` |
   | `kayak-config-drift.service`     | weekly Sun 05:30  | repo→`/etc/` drift detector |
   | `kayak-backup-weekly.service`    | weekly Sun 03:15  | local DB snapshot |
   | `kayak-backup-offsite.service`   | chained (Sun 03:15+) | chained via `OnSuccess=` from `kayak-backup-weekly.service`; rclone upload to gdrive-crypt |
   | `kayak-heartbeat.service`        | weekly Sun 06:00  | positive-signal heartbeat email; meta-monitor (see Phase 1.4 note) |
   | `kayak-recap.service`            | weekly Mon 07:00  | weekly operator recap email |
   | `kayak-audit-gauges.service`     | bimonthly 03:00   | gauge-coverage audit |

   `kayak-fail-test.service` is intentionally NOT in this table — it's the always-fails drill target (Phase 1.5); no `ExecStartPost`, no heartbeat URL.

   For each, add to its `[Service]` section (note the `-` prefix on `ExecStartPost`):
   ```
   ExecStartPost=-/usr/bin/curl -fsS -m 10 --retry 3 -o /dev/null https://hc-ping.com/<uuid>
   ```
   The `-` prefix marks the step ignorable per systemd convention — a heartbeat curl that fails due to a transient network glitch won't cascade into a false `OnFailure=` alert. The successful unit-of-work is still pinged the next run.

   For `Type=oneshot` units (all production kayak-* services here are `Type=oneshot`), `ExecStartPost` runs only on `ExecStart` exit 0 — exactly the success signal we want. Edits go to `systemd/kayak-*.service` in this repo *and* the installed copy at `/etc/systemd/system/kayak-*.service` (per [feedback_systemd_in_tree_copy]). `sudo systemctl daemon-reload` after each batch. **Schedule mapping in healthchecks.io:** the cron form takes the timer's `OnCalendar=` expression 1:1 (e.g. `*:12` → `12 * * * *`); for the multiple hourly units (pipeline, healthcheck, backup-hourly) the simpler "every 60 min" form works too. 14 checks (2026-05-15 count) comfortably fit healthchecks.io's free-tier 20-check ceiling, but the headroom is narrower than the original "8 vs 20" framing — future additions need to watch the cap. **Check-naming convention:** name each healthchecks.io check after its service unit (e.g. `kayak-pipeline.service`) so the dashboard reads as a 1:1 map of the systemd units.

   **Operational note on `kayak-pipeline.service` exit semantics:** `levels pipeline` is designed to keep going on individual-source failures and exit 0 unless something catastrophic broke. The heartbeat ping therefore catches "pipeline didn't run / crashed entirely" but NOT "some sources silently failed." That's by design — `kayak-healthcheck.service` catches stale data; Tier 2.5's `analyze_logs.py` catches per-source failures. Different layers, different signals.

   **Note on `kayak-healthcheck.service`:** its `ExecStart` (`scripts/health-check.sh`) exits 1 on stale data and 2 on missing DB. The heartbeat curl on `ExecStartPost` therefore only fires when *both* the unit ran AND data is fresh — a 2-in-1 signal (unit-alive + data-flowing). A stale-data state and a unit failure both surface to healthchecks.io as "no ping in N minutes," which is fine for Tier 1 but means the dashboard can't distinguish them. Tier 2.1's `/status.json` will separate the two.

   **Note on `kayak-backup-offsite.service`:** it's installed but has no timer of its own — it runs whenever `kayak-backup.service` exits 0 (via `kayak-backup.service:OnSuccess=kayak-backup-offsite.service`). The heartbeat ping confirms the chained offsite upload also ran, not just the local snapshot. Failures during the offsite step route through the offsite unit's own `OnFailure=kayak-notify-failure@%n.service` (already wired) and do not roll back the local backup.

   **Double-alert expectation for the backup chain:** if `kayak-backup.service` itself fails, the chained `kayak-backup-offsite.service` never runs. The operator sees two alerts at different times: an immediate notifier alert (backup failed) AND, later, a healthchecks.io "no ping in N days" alert (offsite stale). This is not noise — they confirm independent facts. The operator-side runbook entry (Tier 4.2) should silence the stale-offsite check until the backup is restored.
4. **Phase 1.4 — Extend the existing failure notifier with an ntfy push channel.** Tier 1.4 is **not** greenfield — `kayak-notify-failure@.service` already exists at `/etc/systemd/system/kayak-notify-failure@.service` (mirrored in `systemd/kayak-notify-failure@.service`), wired to all production `kayak-*` services (14 as of 2026-05-15) via `OnFailure=kayak-notify-failure@%n.service`. The current `ExecStart` logs to syslog and emails pat.kayak@gmail.com via msmtp. **Phase 1.4 adds a parallel ntfy curl** without disturbing the existing syslog + email paths:
   - Add `EnvironmentFile=-/home/pat/.config/kayak/.env` to the unit's `[Service]` section so `$NTFY_TOPIC` resolves.
   - Append a guarded ntfy curl to the existing `ExecStart` shell command, after the existing `mail -s ... | logger ...` line:
     ```
     [ -n "${NTFY_TOPIC:-}" ] && echo "$MSG" | curl -fsS -m 10 --retry 3 -d @- "ntfy.sh/$NTFY_TOPIC" \
         -H "Title: Kayak: %i failed" -H "Priority: high" 2>&1 | logger -t kayak-alert -p user.err
     ```
   - The `[ -n "${NTFY_TOPIC:-}" ]` guard keeps the unit working when `NTFY_TOPIC` is unset (e.g. Phase 1.4 lands before Phase 1.1, or the env var is later rotated out). Pipe-to-logger preserves the "broken channel doesn't silently drop" invariant the existing template establishes.
   - All 13 services (2026-05-14 count; verify with `grep -l OnFailure=kayak-notify-failure /etc/systemd/system/kayak-*.service | wc -l`) pick up the new channel automatically — no per-service edit needed (the `OnFailure=` wiring is already in place; verified in §Reproduce).
   - **Drill:** instantiate the notifier against a real service name to test the message format: `sudo systemctl start 'kayak-notify-failure@kayak-pipeline.service'`. The notifier doesn't check the unit's actual state — it only uses `%i` — so this safely renders the alert exactly as a real failure would. Verify the message lands in pat.kayak@gmail.com (existing path) AND on the phone via ntfy (new path).
5. **Phase 1.5 — End-to-end drill.** Run three scenarios:
   - **Stopped timer:** `sudo systemctl stop kayak-pipeline.timer`; wait one full window past schedule (>1h since `*:12`). Verify healthchecks.io fires "no ping in N minutes" → email + push.
   - **Failing unit:** create a permanent drill-target unit at `systemd/kayak-fail-test.service` (and installed copy) — a no-arg service that always fails, wired to the same notifier:
     ```
     [Unit]
     Description=Drill target — deliberately fails to verify OnFailure= chain
     OnFailure=kayak-notify-failure@%n.service

     [Service]
     Type=oneshot
     User=pat
     ExecStart=/bin/false
     ```
     Run `sudo systemctl start kayak-fail-test.service`; verify the notifier fires through BOTH the existing email path AND the new ntfy path. Leave the unit installed — it's the canonical drill target for any future notifier change (Tier 4 drills, future channel additions). **It is intentionally outside Phase 1.3's heartbeat table** (no `ExecStartPost`, always fails by design); the production-service count in the verification gate (14 as of 2026-05-15) refers to the production-work units.
   - **Existing weekly heartbeat:** `sudo systemctl start kayak-heartbeat.service` to trigger immediately; verify the "host alive" email still arrives. This is the meta-monitor (`systemd/kayak-heartbeat.sh:1-5`) confirming Phase 1.4's notifier edits didn't break the alert pipeline.

   Document outcomes in `docs/operations.md` (placeholder created in Tier 4 — for now, a drafts file).

**Verification gate (end of Tier 1):**
- All production `kayak-*.service` units ping a healthchecks.io URL on success (14 as of 2026-05-15; verify with `grep -l hc-ping /etc/systemd/system/kayak-*.service | wc -l` plus a spot-check of any unit that pings via `${HC_*_UUID}` env-var indirection rather than a literal `hc-ping.com` URL).
- For an **hourly** timer (pipeline / healthcheck / backup-hourly), a `systemctl stop` fires the alert within ~75 min (the cadence + healthchecks.io's grace period). Daily/weekly/bimonthly timers will take their respective windows; out of scope to drill all fourteen.
- A 503/down site fires a Better Stack alert within a few minutes (the missed-checks threshold the monitor is configured for — at 3-min interval with 2 missed checks: ~6 min)
- Push notification reaches the phone within ~30s of email
- Existing `kayak-heartbeat.service` weekly run still delivers its positive-signal email (verify via Phase 1.5 drill scenario 3, not by waiting a week)

**Cross-plan note:** The editor feature (auth / propose / review) is load-bearing for the maintainer workflow. Tier 1.2's Better Stack uptime monitor catches an outright outage but won't surface a broken editor flow (login / propose / review). Tier 2.2's synthetic content check on `/Oregon.html` catches a build break, not an editor-flow break. Known gap; closing it is Tier 2's scope, not Tier 1.

---

### Tier 2 — Status visibility

**Goal:** At a glance, "is the site healthy?" — both for users and for you.

1. **Phase 2.1 — `/status.json` endpoint.** Add `php/status.php` (lightest path; PHP already serves dynamic pages). Reads `latest_observation` and `latest_gauge_observation` tables. Output:
   ```json
   {
     "build_at": "2026-05-11T17:23:00Z",
     "latest_observation_at": "2026-05-11T17:18:00Z",
     "sources": [
       {"name": "USGS-OGC", "fresh_at": "...", "stale_count": 0, "expired_count": 0},
       ...
     ],
     "fetch_errors_24h": 3
   }
   ```
   Set `Cache-Control: no-cache, max-age=10`. Open CORS for `status.mousebrains.com` so the public status page can fetch it.
2. **Phase 2.2 — Synthetic content check.** Second Better Stack monitor for `https://levels.mousebrains.com/Oregon.html` with keyword check `WKCC River Levels`. Fires if the page is up but empty/error. Optional second keyword check on `/status.json` field `latest_observation_at` being within last 4h.
3. **Phase 2.3 — Public status page.** Add a CNAME at `status.mousebrains.com` → Better Stack's hosted status page (configure to surface both monitors with friendly names). DNS for `mousebrains.com` is Cloudflare (verified 2026-05-12: `liv.ns.cloudflare.com` / `dale.ns.cloudflare.com`), so the change is a click in the Cloudflare dashboard. Free on the current tier. Alternative path if you outgrow Better Stack: hand-rolled HTML at `status.mousebrains.com` reading `/status.json` (more code, more control).
4. **Phase 2.4 — Internal dashboard.** ✅ **Done 2026-05-15** — see `docs/done/PLAN_internal_dashboard.md`. New nginx location at `levels.wkcc.org/_internal/` (also `levels-test.wkcc.org/_internal/`); auth via `require_maintainer()` on the existing `editor_session` cookie (no new credential); `X-Robots-Tag: noindex` set both in nginx and in PHP. The legacy `levels.mousebrains.com` vhost carries a `^~ /_internal/ { return 404; }` guard — `SITE_URL` is `levels.wkcc.org`, so the magic-link login flow lands on the wkcc host post-auth; anchoring the dashboard to mousebrains would break the round-trip. Renders, in one page reading `kayak.db`:
   - Per-source freshness heatmap (green <1h, yellow <6h, red older)
   - Recent fetch error log (last 50 from `~/logs/...` or systemd journal)
   - Last 10 build durations (parsed from journal)
   - DB size growth (last 30 days)
   - Last 50 audit-flagged gauges (from existing audit timer output)
   Implementation: Python CGI or PHP, ~150 lines. **Per [feedback_csp_no_inline]:** any JS goes in an external file; CSP is enforced.
5. **Phase 2.5 — `logs.analyze` migration.** Move `~/logs.analyze` from your home dir into the repo:
   - New script: `scripts/analyze_logs.py` (or a `levels analyze-logs` CLI command if it has enough complexity).
   - New systemd unit: `kayak-analyze-logs.service` + `kayak-analyze-logs.timer` (daily). Heartbeat via Tier 1.3 pattern.
   - Output: emit report by email; if any line is "critical", also push via ntfy.
   - Update [reference_logs_analyze] memory to point at the new repo location and remove the "untracked" qualifier.

**Verification gate (end of Tier 2):**
- `curl https://levels.mousebrains.com/status.json | jq .` returns valid JSON with all documented fields
- `https://status.mousebrains.com` reachable, shows green when healthy
- Internal dashboard requires auth, loads in <1s, shows live data
- Anyone with credentials can answer "is the site healthy?" without ssh

---

### Tier 3 — Deploy automation

**Goal:** Reduce risk of `cp` on the wrong file. Make a deploy reproducible and reversible.

1. **Phase 3.1 — Deploy script.** Idempotent `scripts/deploy.sh`:
   ```
   git pull --ff-only
   uv sync --locked --all-extras
   levels migrate
   sudo systemctl restart 'kayak-*.timer'   # picks up unit changes
   levels build
   # nginx reload only if the deployed nginx-config hash differs
   ```
   Manual today; CI-driven in Phase 3.2. Test from your shell first.
2. **Phase 3.2 — Staging promotion via GHA.** ⏸️ **Deferred to ~T+30** (2026-05-15 chat decision). Pairs with `PLAN_three_instance_layout.md`, which re-opens the staging-host question on a non-single-host basis. Original design preserved below for reference. Push to `main` after CI green triggers a `deploy-staging.yml` workflow that SSHes to the staging host (whichever serves `levels-test.wkcc.org` — confirm in [§Reproduce]) and runs `scripts/deploy.sh`. Use a deploy-only system user (NOT `pat`), restricted to `git pull` + `systemctl restart kayak-*` + `levels build` (no shell). SSH key stored in GHA secrets. Wire in a healthchecks.io heartbeat — failed deploy fires an alert via the Tier 1 path.
3. **Phase 3.3 — Production deploy.** ⏸️ **Deferred to ~T+30** (same chat decision). Tagging `vX.Y.Z` triggers `deploy-prod.yml` which uses GHA's [environments + protection rules](https://docs.github.com/en/actions/deployment/targeting-different-environments) to require a manual approval click before SSHing to the WKCC-branded prod hosts. Same `scripts/deploy.sh`, different target.
4. **Phase 3.4 — Rollback.** Document the procedure in `docs/operations.md` (Tier 4):
   - Re-run `deploy-prod.yml` at the previous SHA via GHA's "re-run jobs" UI; OR
   - SSH to the host, `git checkout <prev-sha> && scripts/deploy.sh`
   Include a "data migrations are forward-only" caveat — rolling back code does NOT roll back DB schema; document the exception list.
5. **Phase 3.5 — Drill.** ⏸️ **Deferred to ~T+30** (depends on 3.2/3.3 landing). Two scenarios:
   - Push a deliberately-broken change (test failure) to a feature branch; confirm CI catches it; confirm staging is unchanged.
   - Push a passing change; confirm staging deploys within 5 min, prod is unaffected; tag a release; confirm prod requires manual approval.

**Verification gate (end of Tier 3):**
- Push to main with green CI updates staging within 5 min — no human ssh
- Tagging a release requires a manual approval click before reaching prod
- Rollback is one command (or one GHA re-run click)
- A failed deploy fires a Tier 1 alert

---

### Tier 4 — Runbook + SLO

**Goal:** System operable by someone other than you for short periods.

1. **Phase 4.1 — Architecture overview.** `docs/operations.md`:
   - One-page system diagram (ASCII is fine; nginx → Python build → SQLite → static HTML + PHP dynamic)
   - Every `kayak-*` systemd unit explained in one sentence
   - Where logs live (`journalctl -u kayak-*`, `~/logs/...`, `/var/log/nginx/...`)
   - Where the DB lives, schema-migration story
   - Upstream data source contacts (USGS rep email, NWRFC contact, USACE district)
   - Where backups live (`/home/pat/backups/backup-*.db.gz` weekly snapshots, out of the repo per review-4 R5.6; Hetzner disk-level daily; rclone `gdrive-crypt:` offsite — see `docs/offsite-backup.md`)
   - Pointer to `docs/slo.md` and the runbook entries
2. **Phase 4.2 — Common-failure runbooks.** Per-failure entries in `docs/operations.md` or sub-files:
   - **DB corruption:** restore from a local snapshot at `/home/pat/backups/backup-YYYYMMDDTHHMMSSZ.db.gz` (gzipped; written weekly by `kayak-backup.service`) or from the rclone offsite copy (`docs/offsite-backup.md` exists; cross-link). Per [feedback_never_overwrite_db], take a fresh `sqlite3 .backup` of the live DB first.
   - **Build pipeline stuck:** `journalctl -u kayak-pipeline.service -n 100` to diagnose; `sudo systemctl restart kayak-pipeline.timer` (build runs as the last stage of the pipeline since the build.py split)
   - **Source feed broken:** find in `data/sources.yaml`, set `disabled: true`, file with vendor, redeploy
   - **SSL cert expired:** `sudo certbot renew --dry-run` first; then real renew; `nginx -s reload`
   - **Disk full:** `levels decimate`; check `~/logs/`; check `/tmp/`
   - **nginx misconfig after deploy:** `sudo nginx -t` to diagnose; revert via Phase 3.4 rollback
   - **healthchecks.io firing but everything looks fine:** check timer cadence vs healthchecks.io expected schedule
3. **Phase 4.3 — SLO definition.** `docs/slo.md` (draft targets as
   proposed here; `docs/slo.md` is canonical and has since diverged —
   e.g. data freshness is now a 3 h global window plus a 14 d
   per-source dead-feed detector, 2026-06-03):
   - **Uptime:** 99% / month (allows ~7h downtime). Measured by Better Stack monitor.
   - **Build cadence:** ≥1 successful build/h, 95% of hours/month. Measured by `kayak-pipeline.service`'s healthchecks.io heartbeat (build runs as the last stage of the pipeline since the build.py split).
   - **Data freshness:** ≥1 fresh observation per active source within 2h, 90% of the time. Measured by `/status.json` historical scrape.
   - **Response time:** p95 < 2s for `Oregon.html`. Measured by Better Stack.
   - Quarterly review: did we hit each SLO? If chronically failing, either fix the system or relax the SLO.
4. **Phase 4.4 — Recovery drill.** ⏸️ **Deferred to ~T+30** (2026-05-15 chat decision; pairs with 3.5's deploy drill so both drill scenarios run together). Restore `kayak.db` from rclone offsite into a fresh container or temporary VM — *with the runbook in front of you, not from memory*. Note every gap (a command that didn't work, a path that was wrong, a credential you needed but couldn't find). Re-run after fixing the runbook. Repeat at least annually. Log dates of drills in `docs/operations.md`.
5. **Phase 4.5 — Bus-factor light.** Identify one trusted person (kayak club admin? friend who codes?). Walk them through `docs/operations.md` in person. Get them ssh access (read-only first via a separate user; documented escalation to apply runbook entries). Update runbook with their feedback. They're the contact if you're unreachable for >48h.

**Verification gate (end of Tier 4):**
- `docs/operations.md` exists, references current systemd units (no stale names from earlier project history)
- Each runbook entry has been tested at least once (drill log)
- SLO is measurable using existing observability (Tiers 1-2)
- One non-Pat person has done a successful walk-through of the runbook

## Risks

- **SaaS lock-in.** healthchecks.io / Better Stack / ntfy free tiers can change terms or discontinue. Mitigation: keep the integration thin (a curl + an env var); each can be replaced by self-hosted equivalents (`upptime`, `cabot`/`cronitor`, self-hosted ntfy) in a few hours.
- **Push-notification fatigue.** Alerts that fire too often get muted. Tier 1.5 drill must tune cadences; if ntfy fires more than ~once/week steady-state, the alert is too noisy or the underlying problem needs fixing.
- **GHA-deploys-to-prod attack surface.** GHA having SSH access to prod is real risk. Mitigation: deploy-only system user (not `pat`), restrict to `git pull` + restart kayak units, no shell. Audit workflow permissions before enabling Phase 3.2.
- **Status page false confidence.** A green status that only checks `Oregon.html` will lie if Idaho's parser silently breaks. Tier 2.1's `/status.json` per-source freshness exists for this; status page must surface staleness, not just up/down.
- **Runbook rot.** Untested runbooks are worse than no runbook. Tier 4.4 drill discipline is the only mitigation.
- **Cost creep.** Free tiers cover the start. Both Better Stack and healthchecks.io have paid tiers if/when you outgrow them — check current pricing at the time. Set a budget alarm and decide on a monthly cap before signing up to either.
- **DB-restore drill needs care.** Tier 4.4 should NOT clobber the live DB. Per [feedback_never_overwrite_db]: backup live DB first (`sqlite3 .backup`, not cp — it's WAL-mode), restore into a temporary path, verify, *then* discuss whether to swap.
- **Notifier silent-failure.** `kayak-notify-failure@.service` has no `OnFailure=` of its own (would recurse). If msmtp config breaks (Gmail app-password rotated) or the ntfy curl times out, the operator can be silently un-notified. Mitigation: the unit's existing `2>&1 | logger -t kayak-alert -p user.err` pattern always writes to syslog before attempting email or push — `journalctl -t kayak-alert` always has the source-of-truth record, and the Tier 2.5 `analyze_logs.py` will surface a high rate of `kayak-alert` entries in its daily report.

## Out of scope

- **Centralized log aggregation** (Loki, ELK). `journalctl` + the migrated `analyze_logs.py` is sufficient at this scale. Revisit if log volume outpaces a human's reading speed.
- **Real metrics + Grafana stack.** `/status.json` + a hand-rolled internal dashboard suffice for one host. Revisit if redundancy or multi-tenant lands.
- **Multi-region failover / hot standby.** Separate plan; that's *redundancy*, not *production discipline*. Hetzner backups already cover disk loss; operator-unavailability is the harder problem and Tier 4.5 is the proportional answer.
- **24/7 on-call rotation.** No SLA, no rotation. Email + push is the proportional channel for a club site.
- **PagerDuty/Opsgenie tier.** Adopt only if a real SLA appears.
- **Dependency scanning / SAST / SBOM.** Real concerns but their own plan; not bundled here.

## Reproduce

Read-only commands to verify current state before starting Tier 1.

```bash
# What systemd timers do we have? (each needs a heartbeat in Tier 1.3)
systemctl list-timers --all "kayak-*"

# What units are they wired to, and is anything already set up for OnFailure?
# Enumerate by unit name (the timer column in `list-timers` is unstable).
for u in $(systemctl list-units --type=timer --all 'kayak-*' --no-legend 2>/dev/null | awk '{print $1}'); do
  echo "=== $u ==="
  systemctl cat "${u/.timer/.service}" 2>/dev/null | grep -E "^(ExecStart|ExecStartPost|OnFailure|EnvironmentFile)="
done

# Existing OnFailure notifier (Phase 1.4 extends this — it's NOT greenfield)
systemctl cat kayak-notify-failure@ 2>/dev/null | head -20

# Existing meta-monitor (weekly positive-signal heartbeat email) — Phase 1.5 drill
# uses this to confirm Phase 1.4's notifier edits didn't break the alert chain.
systemctl cat kayak-heartbeat.service 2>/dev/null | head -10
cat systemd/kayak-heartbeat.sh 2>/dev/null | head -5

# Where does logs.analyze live and is it scheduled?
ls -la ~/logs.analyze 2>/dev/null
crontab -l 2>/dev/null | grep -i analyze
systemctl list-timers --all 2>/dev/null | grep -iE "analyze|log"

# nginx vhost layout — confirm levels.mousebrains.com / levels-test / levels.wkcc
sudo nginx -T 2>/dev/null | grep -E "server_name|listen " | head -60

# DNS for mousebrains.com — re-confirm registrar (2026-05-12: Cloudflare —
# `liv.ns.cloudflare.com` / `dale.ns.cloudflare.com`. Tier 2.3 will add a
# CNAME for status.mousebrains.com via the Cloudflare dashboard).
dig +short mousebrains.com NS

# Anything currently exposing /status?
curl -sI https://levels.mousebrains.com/status.json
curl -sI https://levels.mousebrains.com/status

# Backup + offsite story (Tier 4.4 will exercise these)
ls -la /home/pat/backups/ 2>/dev/null | head -5
rclone listremotes 2>/dev/null | grep -i gdrive

# Existing CI surface (Tier 3 builds on this)
cat .github/workflows/ci.yml | head -30

# Confirm the existing notifier name — Phase 1.4 EXTENDS this unit
# (it's `kayak-notify-failure@.service`, NOT `kayak-failure-notify@.service` —
# the underscore vs hyphen ordering matters in the OnFailure= target).
ls /etc/systemd/system/ | grep -i 'notify\|failure'

# OnSuccess-chained services (timer-less) — Phase 1.3 must include these
# in its heartbeat list. `list-timers` misses them.
for u in $(systemctl list-units --type=service --all 'kayak-*' --no-legend 2>/dev/null | awk '{print $1}'); do
  src=$(systemctl cat "$u" 2>/dev/null | grep -E '^(OnSuccess|OnFailure)=' | head -2)
  [ -n "$src" ] && echo "$u: $src"
done
```
