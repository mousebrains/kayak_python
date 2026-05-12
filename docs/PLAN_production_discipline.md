# Plan — Production discipline for kayak

> **Cross-check:** plan drafted 2026-05-11 against the live host (`levels.mousebrains.com`, single Hetzner VPS, sole operator). A second Claude session should re-run the read-only commands in **§Reproduce** to confirm the current-state findings before any tier starts.
>
> Dates are absolute. References are to live systemd units, files in this repo, and external services.

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
- **Public status page DNS:** `status.mousebrains.com` (new A record under whatever registrar holds `mousebrains.com`).
- **Internal dashboard DNS:** `levels.mousebrains.com/_internal/` (vhost subpath, basic-auth or IP-allowlist, `noindex`). No new DNS.
- **Per [feedback_no_sudo]:** all `/etc/` edits (systemd units, nginx vhosts, certbot) get prepared as diffs that you apply. Per [feedback_systemd_in_tree_copy]: every `/etc/systemd/system/kayak-*` patch also goes into the repo's installed location at the same time.

## Target shape

| Tier | Delivers | SaaS | Self-host |
|---|---|---|---|
| 1 — Crash detection | Email + push within minutes of: site down, missed timer, unit failure | Better Stack (uptime), healthchecks.io (heartbeats), ntfy (push) | systemd `OnFailure=` template + per-timer `ExecStartPost` curl |
| 2 — Status visibility | Public status page; internal dashboard; queryable `/status.json` | Better Stack hosted status page | `/status.json` endpoint; internal dashboard at `levels.mousebrains.com/_internal/`; `logs.analyze` migrated into repo |
| 3 — Deploy automation | Push to main → tests pass → auto-deploy to staging; tagged release → manual approval → prod | GitHub Actions | `scripts/deploy.sh`; rollback procedure |
| 4 — Runbook + SLO | `docs/operations.md`; defined SLO; recovery drill done with someone else at the keyboard | — | Runbook; `docs/slo.md`; quarterly drill log |

## Migration tiers

Each tier is several phases; **review gate between tiers**, not between phases.

### Tier 1 — Crash detection

**Goal:** Know within minutes when the site is down or a scheduled job stopped running.

1. **Phase 1.1 — Notification routes.** Create ntfy.sh topic with a hard-to-guess name. Test with `curl -d "test" ntfy.sh/<topic>`. Save as `NTFY_TOPIC` in `~/.config/kayak/.env`. Subscribe on phone (ntfy app). Verify push within 30s.
2. **Phase 1.2 — External uptime monitor.** Sign up for Better Stack free tier. Create monitor for `https://levels.mousebrains.com` (HEAD or GET, expect 200, frequent check interval per the free tier's allowance). Add notification channel: email + ntfy webhook (Better Stack supports custom webhooks). Test by pausing nginx briefly on a quiet evening; confirm alert + recovery.
3. **Phase 1.3 — Heartbeat per systemd timer.** healthchecks.io account; create one check per timer (enumerate via `systemctl list-timers --all "kayak-*"`). For each `.service` triggered by a `kayak-*.timer`, add to its `[Service]` section:
   ```
   ExecStartPost=/usr/bin/curl -fsS -m 10 --retry 3 -o /dev/null https://hc-ping.com/<uuid>
   ```
   For `Type=oneshot` units, `ExecStartPost` runs only on `ExecStart` exit 0 — exactly the success signal we want. Edits go to `deploy/systemd/kayak-*.service` in this repo *and* the installed copy at `/etc/systemd/system/kayak-*.service` (per [feedback_systemd_in_tree_copy]). `systemctl daemon-reload` after each. Tune the per-check schedule expectation in healthchecks.io to match each timer's cadence.
4. **Phase 1.4 — `OnFailure=` template.** Add a generic failure notifier:
   - New file: `deploy/systemd/kayak-failure-notify@.service` (and install copy):
     ```
     [Unit]
     Description=Notify ntfy on kayak unit failure: %i

     [Service]
     Type=oneshot
     EnvironmentFile=/home/pat/.config/kayak/.env
     ExecStart=/usr/bin/bash -c 'systemctl status %i --no-pager -n 20 | curl -fsS -d @- ntfy.sh/$NTFY_TOPIC -H "Title: kayak unit failed: %i" -H "Priority: high"'
     ```
   - Each `kayak-*.service` gets `OnFailure=kayak-failure-notify@%n.service` in its `[Unit]` section.
   - Drill: `sudo systemctl start kayak-failure-notify@kayak-test.service` with a deliberately failing unit; verify push.
5. **Phase 1.5 — End-to-end drill.** Run two scenarios:
   - **Stopped timer:** `sudo systemctl stop kayak-fetch.timer`; wait one full window past schedule. Verify healthchecks.io fires "no ping in N minutes" → email + push.
   - **Failing unit:** modify a kayak service to `exit 1` for one run; verify `OnFailure=` template fires.
   Document outcomes in `docs/operations.md` (placeholder created in Tier 4 — for now, a drafts file).

**Verification gate (end of Tier 1):**
- All `kayak-*` timers ping a healthchecks.io URL on success
- A killed timer fires an alert within one cadence window
- A 503/down site fires an alert within a few minutes (the missed-checks threshold the monitor is configured for)
- Push notification reaches the phone within ~30s of email

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
3. **Phase 2.3 — Public status page.** Add A record `status.mousebrains.com` → Better Stack's hosted status page (configure to surface both monitors with friendly names). Free on the current tier. Alternative path if you outgrow Better Stack: hand-rolled HTML at `status.mousebrains.com` reading `/status.json` (more code, more control).
4. **Phase 2.4 — Internal dashboard.** New nginx location at `levels.mousebrains.com/_internal/` (basic-auth via `htpasswd` or IP-allowlist; `add_header X-Robots-Tag noindex`). Renders, in one page reading `kayak.db`:
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
2. **Phase 3.2 — Staging promotion via GHA.** Push to `main` after CI green triggers a `deploy-staging.yml` workflow that SSHes to the staging host (whichever serves `levels-test.wkcc.org` — confirm in [§Reproduce]) and runs `scripts/deploy.sh`. Use a deploy-only system user (NOT `pat`), restricted to `git pull` + `systemctl restart kayak-*` + `levels build` (no shell). SSH key stored in GHA secrets. Wire in a healthchecks.io heartbeat — failed deploy fires an alert via the Tier 1 path.
3. **Phase 3.3 — Production deploy.** Tagging `vX.Y.Z` triggers `deploy-prod.yml` which uses GHA's [environments + protection rules](https://docs.github.com/en/actions/deployment/targeting-different-environments) to require a manual approval click before SSHing to the WKCC-branded prod hosts. Same `scripts/deploy.sh`, different target.
4. **Phase 3.4 — Rollback.** Document the procedure in `docs/operations.md` (Tier 4):
   - Re-run `deploy-prod.yml` at the previous SHA via GHA's "re-run jobs" UI; OR
   - SSH to the host, `git checkout <prev-sha> && scripts/deploy.sh`
   Include a "data migrations are forward-only" caveat — rolling back code does NOT roll back DB schema; document the exception list.
5. **Phase 3.5 — Drill.** Two scenarios:
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
   - Where backups live (Hetzner snapshots + rclone offsite)
   - Pointer to `docs/slo.md` and the runbook entries
2. **Phase 4.2 — Common-failure runbooks.** Per-failure entries in `docs/operations.md` or sub-files:
   - **DB corruption:** restore from `~/backups/kayak.db.<date>` or rclone offsite (`docs/offsite-backup.md` exists; cross-link)
   - **Build pipeline stuck:** `journalctl -u kayak-build` to diagnose; `sudo systemctl restart kayak-fetch.timer`
   - **Source feed broken:** find in `data/sources.yaml`, set `disabled: true`, file with vendor, redeploy
   - **SSL cert expired:** `sudo certbot renew --dry-run` first; then real renew; `nginx -s reload`
   - **Disk full:** `levels decimate`; check `~/logs/`; check `/tmp/`
   - **nginx misconfig after deploy:** `sudo nginx -t` to diagnose; revert via Phase 3.4 rollback
   - **healthchecks.io firing but everything looks fine:** check timer cadence vs healthchecks.io expected schedule
3. **Phase 4.3 — SLO definition.** `docs/slo.md`:
   - **Uptime:** 99% / month (allows ~7h downtime). Measured by Better Stack monitor.
   - **Build cadence:** ≥1 successful build/h, 95% of hours/month. Measured by healthchecks.io.
   - **Data freshness:** ≥1 fresh observation per active source within 2h, 90% of the time. Measured by `/status.json` historical scrape.
   - **Response time:** p95 < 2s for `Oregon.html`. Measured by Better Stack.
   - Quarterly review: did we hit each SLO? If chronically failing, either fix the system or relax the SLO.
4. **Phase 4.4 — Recovery drill.** Restore `kayak.db` from rclone offsite into a fresh container or temporary VM — *with the runbook in front of you, not from memory*. Note every gap (a command that didn't work, a path that was wrong, a credential you needed but couldn't find). Re-run after fixing the runbook. Repeat at least annually. Log dates of drills in `docs/operations.md`.
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
for t in $(systemctl list-timers --all "kayak-*" --no-legend 2>/dev/null | awk '{print $NF}'); do
  echo "=== $t ==="
  systemctl cat "${t/.timer/.service}" 2>/dev/null | grep -E "^(ExecStart|ExecStartPost|OnFailure|EnvironmentFile)="
done

# Where does logs.analyze live and is it scheduled?
ls -la ~/logs.analyze 2>/dev/null
crontab -l 2>/dev/null | grep -i analyze
systemctl list-timers --all 2>/dev/null | grep -iE "analyze|log"

# nginx vhost layout — confirm levels.mousebrains.com / levels-test / levels.wkcc
sudo nginx -T 2>/dev/null | grep -E "server_name|listen " | head -60

# DNS for mousebrains.com — confirm registrar so status.mousebrains.com can be added
dig +short mousebrains.com NS

# Anything currently exposing /status?
curl -sI https://levels.mousebrains.com/status.json
curl -sI https://levels.mousebrains.com/status

# Backup + offsite story (Tier 4.4 will exercise these)
ls -la ~/backups/ 2>/dev/null | head -5
rclone listremotes 2>/dev/null | grep -i gdrive

# Existing CI surface (Tier 3 builds on this)
cat .github/workflows/ci.yml | head -30

# Confirm no existing /etc/systemd/system/kayak-failure-notify@.service
ls /etc/systemd/system/ | grep -i failure
```
