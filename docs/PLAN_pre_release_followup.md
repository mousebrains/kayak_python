# Plan — Pre-release follow-up after 2026-05-13 audit pass

**Status:** In progress (as of 2026-05-14). P0.1, P0.2, all 12 Quick Wins,
T1.1, T1.2, T1.4 landed; T1.5 in flight. Tier 2 (test/CI maturity) and
Tier 3 (architecture) not yet started.

> **Cross-check:** Plan drafted 2026-05-13 against `main` after a three-iteration audit pass that ran 15 parallel investigations (security, Python quality, PHP quality, testing/CI, ops, DB, docs, architecture, deps, repo hygiene, data-loss scenarios, editor-flow attacks, frontend a11y, doc drift, claim verification). The audit was harsh-grading and the overall verdict was **B−** — ship-quality after a small set of fixes, but several gaps stand between this and what a Series-B company would adopt as a critical dependency. This plan consolidates the audit's P0/P1/tier-roadmap output.
>
> Where this plan and [`PLAN_production_discipline.md`](PLAN_production_discipline.md) overlap, **production-discipline is the source of truth** for deploy.sh / runbook / SLO / drill items; this plan cross-references rather than duplicating. New material is the cert-expiry monitor, the test/CI maturity tier, the architecture tier, and a handful of Quick Wins.
>
> **Iter log:**
> - iter 1 (2026-05-13): 11 findings — Initial pass missed that (a) `PLAN_production_discipline.md` already owns the deploy.sh and runbook work — this plan was duplicating; refactored to cross-reference. (b) Cert monitor as drafted required root (read `/etc/letsencrypt/live/`) and broke the `User=pat` pattern; rewrote to be live-probe-only so it fits the existing sandbox shape. (c) Renewal dry-run does need root (writes `/var/log/letsencrypt`); kept that as a separate weekly unit. (d) Pre-cutover cert is 2-SAN, post-cutover is 3-SAN; `EXPECTED_SANS` env was missing, added with the operator update step. (e) Quick Wins list initially mixed 1-line code edits with 1-day refactors; split into two buckets. (f) Tier 1 listed both atomic deploy.sh AND simple fail-fast pipeline; the latter is a 5-line code change, not Tier-1 operability work — moved to Quick Wins. (g) Tier 3 dormant-schema cleanup conflicted with the test-schema-parity check in Tier 2 — sequenced T2.7 before T3.5. (h) The 3 frontend Highs were unassigned; added to Quick Wins. (i) Initial cert-script used `set -euo pipefail` plus a `for` over command substitution that hides the inner `openssl` exit code; restructured to capture and check explicitly. (j) Missed that `request_terminate_timeout` is absent from `kayak-fpm-pool.conf` (doc-drift agent noted but plan didn't have a task); added QW.3. (k) `gitleaks` already runs in pre-commit + CI; the audit's "add `.gitleaks.toml`" was framed as hygiene, not a gap — kept in Tier 2 but flagged as low-value.
> - iter 2 (2026-05-13): 5 findings — (a) Cert script's "3 retries with 2s sleep" can interact with hc-ping's 10s timeout; tightened to 8s connect + 3 attempts but bounded the total wall time. (b) The two cert timers should not fire on the same hour as `certbot.timer` itself (whose schedule is OS-managed and runs ~twice daily); chose 06:30 daily for the expiry probe and Mon 04:15 for the renewal dry-run, both outside likely certbot windows. (c) Quick Win on `request_terminate_timeout` interacts with long-running PHP requests; verified no kayak handler is expected to run >30s (per `php/edit.php`, `propose_handler.php`, `review_logic.php` — all sub-second). (d) Tier 2 property tests for parsers shouldn't fire on every CI run if they're slow; scoped to `@pytest.mark.property` and a separate CI job. (e) Tier 3.4 (KAYAK_HOME) needs to land AFTER the deploy.sh in production-discipline.md Tier 3, since deploy.sh will read $KAYAK_HOME; documented the dependency.
> - iter 3 (2026-05-13): 2 findings — (a) The cert renewal dry-run runs `certbot renew --dry-run` which, for the levels.mousebrains.com cert, uses the nginx authenticator (per `/etc/letsencrypt/renewal/levels.mousebrains.com.conf` post-Phase-4 of DNS.CHANGEOVER.md). Pre-Phase 4, the renewal config is `authenticator=manual` from the DNS-01 acquisition; dry-run will *fail* because manual auth needs human input. This is exactly the bug we want the monitor to catch — but if the monitor is installed BEFORE Phase 4 of DNS.CHANGEOVER.md, it will alert immediately. Resolution: install the monitor scripts now (P0.2) but enable the weekly renewal-test timer ONLY AFTER Phase 4 step 3 restores the nginx authenticator. Documented in P0.2 §Sequencing. (b) The `mbstring` removal (P0.1) could in principle break a transitive dev-time dependency. Verified: `vendor/composer/installed.json` shows zero packages requiring `ext-mbstring` (php-cs-fixer, phpstan, phpunit all check at runtime but don't `require` it in `composer.json`). Removal is safe.
> - iter 4 (2026-05-13): 1 finding — Tier 2.2 property tests need a seed-pinning strategy so failures are reproducible across CI runs. Added the standard `@settings(derandomize=True)` + Hypothesis database directive. Convergence: 11 → 5 → 2 → 1. Stopping; remaining items are aesthetic.
>
> Dates absolute. References `file:line` against current `main`.

## Why

The 2026-05-13 audit pass surfaced ~35 findings across security, Python/PHP code quality, testing/CI, ops, DB, docs, architecture, deps, and frontend. Verification reduced overstated claims (notably: nginx already redacts magic-link tokens — the audit's initial High was a false positive). Net assessment:

| Dimension | Grade | What's good | What's the gap |
|---|---|---|---|
| Dependency hygiene | A | `pip-audit --strict` blocks CI; CVE-aware floors | small hygiene wins only |
| Python quality | A− | mypy/ruff clean; zero High findings | pipeline error swallowing; missing tracebacks |
| Security | B+ | 11/14 attack scenarios blocked; SSRF/calc-eval/CSRF solid | magic-link UX trade-off (accepted) |
| Frontend / a11y / SEO | B+ | CSP-strict; mobile-aware; print styles | 3 specific bugs (HUC pill duplicate, sparkline aria chain, hardcoded weather link) |
| Repo hygiene | B | Tracked surface lean; pre-commit comprehensive | leaked legacy MySQL pw in history (rotated); stale CHANGELOG |
| Database | B | WAL+pragmas correct; migrations transactional | test schema bypasses migrations; `change_request` retention unbounded |
| PHP quality | B | PHPStan L8 clean; CSRF/headers/cookies clean | emulated prepares not pinned; TZ hazard on `strtotime`; `_<file>_` convention drift |
| Architecture | B− | Two-layer split clean | parser does IO; pipeline no stage gating; `/home/pat` welded in |
| Testing / CI | B− | All suites green; coverage gate 75% | **CI installs `mbstring` while prod lacks it**; tautological pipeline test; no editor E2E |
| Documentation | C+ | License story clean; PLAN_*.md plentiful | README install path broken; no operator runbook; schema doc stale |
| Ops / deploy | C+ | Strong base (systemd sandbox, nginx hardening, fail2ban) | manual deploys; weekly-only backups (RPO 7d); restore never drilled; **no cert-expiry alerting** |

This plan addresses the items not already owned by [`PLAN_production_discipline.md`](PLAN_production_discipline.md).

## Constraints

- **Single operator.** Effort budget is hours-per-week, not engineer-days. The plan tiers are ordered by ROI; partial completion is fine.
- **Cutover is live.** DNS.CHANGEOVER.md is mid-flight. Don't introduce monitors that page during the known-bad cutover window (see iter 3, finding a).
- **No new SaaS beyond what's already chosen.** healthchecks.io for heartbeats; ntfy.sh for push; Better Stack for uptime. Cert monitor reuses healthchecks.io.
- **Per `[feedback_no_sudo]`:** every `/etc/`-touching change ships as a repo diff + a manual apply step.

---

## P0 — Pre-release blockers

P0 items must land before or alongside the DNS cutover.

### P0.1 — Remove `mbstring` from CI

**Why.** `CLAUDE.md:103` documents "PHP-FPM in prod **lacks mbstring**." `.github/workflows/ci.yml:38` installs `mbstring`. Today's working tree has zero `mb_*` calls (verified), so the practical risk is small — but the mismatch silently masks any future contributor's `mb_strlen` call, which would pass CI and 500 in prod. **Removing `mbstring` from CI is the right call** because (a) it's already not used, (b) prod can't easily gain it without an FPM-pool reconfiguration, and (c) the asymmetry currently runs the wrong direction (more in CI than prod).

**Change.** In `.github/workflows/ci.yml`, the line `extensions: pdo_sqlite, curl, mbstring` becomes `extensions: pdo_sqlite, curl`. Verify locally with:

```bash
grep -rn 'mb_' src/ php/ scripts/ tests/php/ 2>/dev/null
# expect: no output
```

**Verify dep transitives.** `vendor/composer/installed.json` shows no package requires `ext-mbstring` in its composer.json. Confirmed via iter 3.

**Effort.** 5 minutes. Done in same PR as P0.2.

### P0.2 — Cert-expiry monitor (Let's Encrypt)

**Why.** Audit finding OPS-H: certbot.timer is OS-managed and unmonitored. If renewal silently fails (the obvious post-cutover regression is `authenticator=manual` left in renewal config from Phase 2 of DNS.CHANGEOVER.md), the cert expires 60-90 days later with no warning, the site goes hard-fail-handshake, and you find out from a user. The fix is two thin systemd timers + two scripts + one healthchecks.io check per timer.

**Threat model — what we want to detect:**

| Failure | Caught by | Lead time |
|---|---|---|
| `certbot.timer` disabled | daily expiry probe (drops below 21 days) | ≥ 21 days |
| Renewal config broken (e.g. `authenticator=manual` left over) | weekly renewal dry-run | ≤ 7 days |
| Cert renewed but nginx not reloaded | daily expiry probe (live-probes each hostname) | ≤ 24 hours |
| Cert expired | daily expiry probe (CRITICAL exit) | ≤ 24 hours, but you should have seen WARN first |
| Cert missing a required SAN (e.g. forgot to add `levels.wkcc.org` in Phase 2) | daily expiry probe (SAN coverage check) | ≤ 24 hours |
| Script itself stops running | hc-ping "ping not received" notification | ≤ 25 hours (24h cadence + 1h grace) |

**Design choices (and what they cost):**

- **Live TLS probe, not file read.** The script connects to each hostname over TCP/443 and reads the served cert. This means (a) no root needed — runs as `User=pat`, fits the existing sandbox pattern; (b) it catches "renewed but nginx not reloaded" automatically (the file would be fresh but the served cert wouldn't be); (c) it requires the network probe to succeed, so a transient nginx blip during a reload could trigger a false alarm. Mitigated by **3 attempts with 2-second sleeps inside an 8-second per-attempt timeout** — the whole script bounds at ~30 seconds.
- **Two separate timers.** Daily expiry probe (cheap, no root). Weekly renewal dry-run (requires root because certbot writes `/var/log/letsencrypt/`). Splitting them keeps the daily check in the existing sandbox pattern and limits the root-running surface to once a week.
- **Three thresholds.** OK ≥ 21 days. WARN 7-20 days (something has failed; you have time). CRITICAL < 7 days or hard-fail (act today). Maps to systemd exit 0 / 1 / 2; `OnFailure=kayak-notify-failure@%n` fires for nonzero exit; the corresponding hc-ping check fires "ping not received" if the unit never runs.
- **`EXPECTED_SANS` is env-configurable.** Pre-cutover: `"levels.mousebrains.com levels-test.wkcc.org"`. Post-cutover (after DNS.CHANGEOVER.md Phase 2 cert acquisition): add `levels.wkcc.org`. Operator updates the env file at cutover time.

**Files to add:**

```
scripts/check-cert-expiry.sh                  # live-probe daily check
systemd/kayak-cert-expiry.service             # User=pat oneshot
systemd/kayak-cert-expiry.timer               # OnCalendar=*-*-* 06:30, RandomizedDelaySec=15min

systemd/kayak-cert-renewal-test.service       # User=root weekly certbot --dry-run
systemd/kayak-cert-renewal-test.timer         # OnCalendar=Mon *-*-* 04:15
```

Plus two new env vars in `~/.config/kayak/.env`:

```
HC_CERT_EXPIRY=https://hc-ping.com/<uuid-1>
HC_CERT_RENEWAL_TEST=https://hc-ping.com/<uuid-2>
EXPECTED_SANS="levels.mousebrains.com levels-test.wkcc.org"
```

**`scripts/check-cert-expiry.sh`:**

```bash
#!/usr/bin/env bash
# Live-probe each expected hostname to verify the Let's Encrypt cert is
# healthy. No root required — runs as User=pat from the systemd unit.
#
# Exits 0 if all probed hosts return a cert with >= WARN_DAYS remaining
#       and the union of served SANs covers EXPECTED_SANS.
# Exits 1 (WARN) if remaining days are in [CRIT_DAYS, WARN_DAYS) — the
#       cert hasn't renewed yet but isn't critical.
# Exits 2 (CRITICAL) if the cert has < CRIT_DAYS remaining, a hostname
#       fails to return a cert in 3 attempts, or any expected SAN is
#       missing from the served union.
#
# OnFailure=kayak-notify-failure@%n.service fires on exit 1 or 2.
# A healthy run posts to ${HC_CERT_EXPIRY} via ExecStartPost.
set -euo pipefail

WARN_DAYS="${WARN_DAYS:-21}"
CRIT_DAYS="${CRIT_DAYS:-7}"
EXPECTED_SANS="${EXPECTED_SANS:-levels.mousebrains.com levels-test.wkcc.org}"

log() { printf '[cert-check] %s\n' "$*"; }

probe_cert_enddate() {
    # Echo notAfter on stdout, or empty string on failure.
    local host=$1
    for attempt in 1 2 3; do
        local out
        out=$(timeout 8 openssl s_client -servername "$host" \
              -connect "$host:443" </dev/null 2>/dev/null \
              | openssl x509 -noout -enddate 2>/dev/null \
              | cut -d= -f2 || true)
        if [ -n "$out" ]; then
            printf '%s' "$out"
            return 0
        fi
        sleep 2
    done
    return 1
}

probe_cert_sans() {
    local host=$1
    timeout 8 openssl s_client -servername "$host" \
        -connect "$host:443" </dev/null 2>/dev/null \
        | openssl x509 -noout -ext subjectAltName 2>/dev/null \
        | grep -oE 'DNS:[^,]+' | sed 's/^DNS://' | tr '\n' ' '
}

WORST_DAYS=9999
WORST_HOST=""
declare -A SEEN_SANS=()
NOW_EPOCH=$(date +%s)

for host in $EXPECTED_SANS; do
    END=$(probe_cert_enddate "$host" || true)
    if [ -z "$END" ]; then
        log "FAIL: TLS handshake to $host did not return a cert after 3 attempts"
        exit 2
    fi
    END_EPOCH=$(date -d "$END" +%s)
    DAYS_LEFT=$(( (END_EPOCH - NOW_EPOCH) / 86400 ))
    SANS=$(probe_cert_sans "$host")
    log "$host: notAfter=$END, days_left=$DAYS_LEFT, served_sans=[$SANS]"
    for san in $SANS; do SEEN_SANS[$san]=1; done
    if [ "$DAYS_LEFT" -lt "$WORST_DAYS" ]; then
        WORST_DAYS=$DAYS_LEFT
        WORST_HOST=$host
    fi
done

# Verify every EXPECTED_SAN was served by at least one host.
for san in $EXPECTED_SANS; do
    if [ -z "${SEEN_SANS[$san]:-}" ]; then
        log "FAIL: expected SAN $san not present in any served cert"
        exit 2
    fi
done

if [ "$WORST_DAYS" -lt "$CRIT_DAYS" ]; then
    log "CRITICAL: $WORST_HOST has $WORST_DAYS days remaining (< $CRIT_DAYS)"
    exit 2
fi
if [ "$WORST_DAYS" -lt "$WARN_DAYS" ]; then
    log "WARN: $WORST_HOST has $WORST_DAYS days remaining (< $WARN_DAYS) — renewal should have happened by now"
    exit 1
fi
log "OK: minimum $WORST_DAYS days remaining (on $WORST_HOST); all EXPECTED_SANS verified."
exit 0
```

**`systemd/kayak-cert-expiry.service`:**

```ini
[Unit]
Description=Kayak: daily Let's Encrypt cert health probe
After=network-online.target
Wants=network-online.target
OnFailure=kayak-notify-failure@%n.service

[Service]
Type=oneshot
User=pat
WorkingDirectory=/home/pat/kayak
EnvironmentFile=/home/pat/.config/kayak/.env
ExecStart=/home/pat/kayak/scripts/check-cert-expiry.sh
# `-` prefix: a transient hc-ping curl glitch shouldn't cascade to OnFailure.
ExecStartPost=-/usr/bin/curl -fsS -m 10 --retry 3 -o /dev/null ${HC_CERT_EXPIRY}
TimeoutStartSec=120
Nice=10

# Sandboxing copied from kayak-decimate.service (the canonical User=pat shape).
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
ProtectProc=invisible
ProcSubset=pid
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=
SystemCallFilter=@system-service
SystemCallArchitectures=native
SystemCallErrorNumber=EPERM
UMask=0077
```

**`systemd/kayak-cert-expiry.timer`:**

```ini
[Unit]
Description=Daily Kayak Let's Encrypt cert health probe

[Timer]
OnCalendar=*-*-* 06:30:00
RandomizedDelaySec=15min
Persistent=true
Unit=kayak-cert-expiry.service

[Install]
WantedBy=timers.target
```

**`systemd/kayak-cert-renewal-test.service`:**

```ini
[Unit]
Description=Kayak: weekly certbot renew --dry-run
After=network-online.target
Wants=network-online.target
OnFailure=kayak-notify-failure@%n.service

[Service]
Type=oneshot
User=root
EnvironmentFile=/home/pat/.config/kayak/.env
ExecStart=/usr/bin/certbot renew --dry-run --quiet --non-interactive --no-random-sleep-on-renew
ExecStartPost=-/usr/bin/curl -fsS -m 10 --retry 3 -o /dev/null ${HC_CERT_RENEWAL_TEST}
TimeoutStartSec=300
Nice=15

# Root needs to read /etc/letsencrypt + write /var/log/letsencrypt, plus
# /home/pat/.config/kayak/.env for the HC_CERT_RENEWAL_TEST var. ProtectHome
# is read-only (matches every other kayak-* unit's pattern) so root can still
# read the env file while /home is otherwise locked.
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=/etc/letsencrypt /var/log/letsencrypt /var/lib/letsencrypt
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
UMask=0022
```

**`systemd/kayak-cert-renewal-test.timer`:**

```ini
[Unit]
Description=Weekly Kayak certbot dry-run renewal test

[Timer]
OnCalendar=Mon *-*-* 04:15:00
RandomizedDelaySec=15min
Persistent=true
Unit=kayak-cert-renewal-test.service

[Install]
WantedBy=timers.target
```

**Operator setup (run once on the live host):**

```bash
# 1. Create two healthchecks.io checks (one daily 25h grace, one weekly 8d grace).
#    Name them `kayak-cert-expiry.service` and `kayak-cert-renewal-test.service`
#    to match the systemd dashboard naming convention used by other heartbeats.

# 2. Add the two URLs + EXPECTED_SANS to ~/.config/kayak/.env (chmod 600 already).

# 3. The script lives in-tree; the systemd unit ExecStarts it from there. After
#    `git pull` brings the file to the live host, just make sure it's executable:
chmod +x /home/pat/kayak/scripts/check-cert-expiry.sh

# 4. Install the four systemd units to /etc/systemd/system/ (per [feedback_systemd_in_tree_copy],
#    the repo copy at /home/pat/kayak/systemd/ stays as the source of truth — the install copy
#    is what systemd actually loads).
for u in kayak-cert-expiry.service kayak-cert-expiry.timer \
         kayak-cert-renewal-test.service kayak-cert-renewal-test.timer; do
    sudo install -m 0644 "/home/pat/kayak/systemd/$u" "/etc/systemd/system/$u"
done
sudo systemctl daemon-reload

# 5. Smoke-test the daily check by hand BEFORE enabling the timer.
sudo systemctl start kayak-cert-expiry.service
sudo journalctl -u kayak-cert-expiry --no-pager -n 20
# Expect: [cert-check] OK: minimum N days remaining (on <host>); all EXPECTED_SANS verified.

# 6. Enable the daily timer.
sudo systemctl enable --now kayak-cert-expiry.timer

# 7. The renewal dry-run timer is conditional on cutover state — see §Sequencing below.
```

**Sequencing.** The daily expiry probe (`kayak-cert-expiry.timer`) is safe to enable **now** with `EXPECTED_SANS="levels.mousebrains.com levels-test.wkcc.org"`. The weekly renewal dry-run (`kayak-cert-renewal-test.timer`) gating depends on which cutover path is taken:

- **Original path (`DNS.CHANGEOVER.md`):** wait until Phase 4 step 3 restores the nginx authenticator — until then, `/etc/letsencrypt/renewal/levels.mousebrains.com.conf` has `authenticator = manual` from the Phase 2 DNS-01 acquisition and `certbot renew --dry-run` will fail interactively. Enable post-Phase-4 step 3. Add `EXPECTED_SANS+=" levels.wkcc.org"` to the env file at the same time (single 3-SAN cert after Phase 2).
- **Fast path (`DNS.CHANGEOVER-fastpath.md`):** the bridge cert at `/etc/nginx/certs/levels.wkcc.org.{cert,privkey}` is not certbot-managed; the existing `levels.mousebrains.com` renewal config stays on `authenticator=nginx` throughout. Enable the weekly renewal-test timer at the same time as the daily probe — neither cert path ever puts the renewal config into a state the dry-run can't satisfy. After Phase C (`certbot --nginx --expand --cert-name levels.mousebrains.com -d levels.mousebrains.com -d levels-test.wkcc.org -d levels.wkcc.org`), update `EXPECTED_SANS` to include `levels.wkcc.org`. End-state is a single 3-SAN cert at `/etc/letsencrypt/live/levels.mousebrains.com/` — identical to the original plan's end-state.

**Acceptance.** `journalctl -u kayak-cert-expiry` shows daily OK. Manually break it (`EXPECTED_SANS="bogus.example.com $EXPECTED_SANS" systemctl start kayak-cert-expiry`) and verify the notifier fires. Restore env. Same drill for the renewal test once Phase 4 is done.

**Effort.** 3 hours including the smoke tests and the healthchecks.io setup.

### P0.3 — DreamHost legacy MySQL password rotation (acknowledged)

DreamHost password rotated; full retirement of the legacy MySQL pipeline scheduled for **2026-07-04** or sooner. The leaked credential (`Deschutes` at `f0ade18`, `9f46b40`) is permanent in git history; rotation is sufficient. No further action — do **not** `git filter-repo` it (would invalidate every existing clone, and the credential is already invalid).

---

## Quick Wins (≤ 1 day total)

One-shot fixes for items the audit caught. All are sub-2-hour changes individually; bundling them as one PR is fine.

| # | Change | File | Why | Effort |
|---|---|---|---|---|
| QW.1 | Pin `ATTR_EMULATE_PREPARES = false` in PDO factory | `php/includes/db.php:29` | Defends against future driver swap; today's SQLite uses native prepares implicitly | 5 min |
| QW.2 | Set `php_admin_value[date.timezone] = UTC` in fpm pool | `deploy/kayak-fpm-pool.conf` | 10+ files call `strtotime($row['observed_at'])` without `' UTC'`; pinning the process TZ removes the whole class | 5 min |
| QW.3 | Set `request_terminate_timeout = 30` in fpm pool | `deploy/kayak-fpm-pool.conf` | Doc-drift agent found this missing; no handler is expected to run >30s | 5 min |
| QW.4 | Cap HTTP response body at 50 MB | `src/kayak/utils/http_client.py:297` | Hostile feed currently OOMs the pipeline; replace `resp.text()` with `resp.content.read(50_000_000)` | 30 min + test |
| QW.5 | Pipeline simple fail-fast on `fetch` failure | `src/kayak/cli/pipeline.py:72-86` | Today stale `update-gauge-cache` + `build` run after `fetch` fails. Full DAG is T3.2; this is the 5-line stopgap | 30 min |
| QW.6 | Tighten `kayak-pipeline` `ReadWritePaths` | `systemd/kayak-pipeline.service:25` | Today's `/home/pat` is overbroad; replace with the specific writable subdirs justified by the symlink-rename | 20 min + verify |
| QW.7 | Strip `+tag` from Gmail addresses in `normalize_email` | `php/includes/auth_magic_link.php:35` | Per-email magic-link rate cap currently bypassable via `user+a@gmail.com` aliases | 30 min |
| QW.8a | Duplicate HUC8 filter pill | `src/kayak/web/build/levels.py:319-322` | Cosmetic but visible regression on every state page | 30 min |
| QW.8b | Sparkline `aria-hidden` on outer span | `src/kayak/web/static/levels.js:42-47` + `sparklines.py:91-95` | SR users hear 50+ unlabeled spans per page | 20 min |
| QW.8c | Per-state "Weather" link instead of hardcoded Oregon | `src/kayak/web/build/shell.py:137` | Idaho/Washington/California/Nevada users get Oregon Windy URL | 20 min |
| QW.9 | Add `roave/security-advisories: dev-latest` to composer dev deps | `composer.json` | Free transitive vuln gate | 5 min |
| QW.10 | Add `composer audit` + `npm audit --audit-level=high` to CI | `.github/workflows/ci.yml` | Audit-agent recommendation; small, hygiene-only | 10 min |

Each lands as a one-commit PR with a focused test where applicable. After this bundle the audit's P1 list is fully addressed except for the deeper Tier 1/2/3 items below.

---

## Tier 1 — Operability (~2-3 weeks)

**Objective.** An operator who is not you can recover from any of the top five plausible outages without phoning you.

The bulk of Tier 1 is already specified in [`PLAN_production_discipline.md`](PLAN_production_discipline.md) Tiers 3-4. The audit added these specific items that aren't covered there:

### T1.A — Cross-references to PLAN_production_discipline.md

| Audit finding | Owned by |
|---|---|
| Manual `cp` deploy | production-discipline Tier 3 (deploy.sh) |
| Operator runbook missing | production-discipline Phase 4.2 (`docs/operations.md`) |
| Drift detection between repo and `/etc/` | production-discipline Tier 2.1 indirectly; explicit task to add below |
| Weekly-only backups (RPO 7d) | production-discipline Tier 4 covers cadence policy; concrete task below |
| Restore never drilled | production-discipline Phase 4.4 (restore drill) |
| SLO definition | production-discipline Phase 4.3 |

The plan there is sound. The items below are net-new from this audit:

### T1.1 — Hourly DB backup with WAL checkpoint (RPO ≤ 1h)

**Why.** Today `kayak-backup.timer` fires Sunday 03:15 only (verified at `systemd/kayak-backup.timer:5`). Hourly-collected observation data has a 7-day worst-case RPO. Hourly local backups + the existing weekly offsite gives a 1-hour worst case.

**Change.**

- Add `systemd/kayak-backup-hourly.timer` (`OnCalendar=*-*-* *:38:00`, `RandomizedDelaySec=2min`).
- New `scripts/kayak-backup-hourly.sh`: `sqlite3 $DB "PRAGMA wal_checkpoint(TRUNCATE); .backup $DEST/hourly-$(date -u +%Y%m%dT%H%M%SZ).db"` then keep last 24, rotate.
- Keep current weekly (rename to `kayak-backup-weekly.service`, chains to `kayak-backup-offsite.service`).
- Retention windows: 24 hourly + 14 daily + 4 weekly + offsite-weekly.

**Disk budget.** Current `kayak.db` is sized in the tens of MB; 24 hourly × ~50 MB = ~1.2 GB. Acceptable on a 40 GB Hetzner CPX21 VPS.

**Acceptance.** `ls /home/pat/backups/hourly/` shows 24 windowed files; restore from any one of them succeeds (verified by T1.4 drill).

**Effort.** 0.5 day.

**Depends on.** Nothing.

### T1.2 — Config drift detection

**Why.** Drift between `conf/` + `deploy/` + `systemd/` and `/etc/` is detected today only when a human runs `scripts/sysinfo-for-review.sh`. The 2026-05-10 preflight proved the value; making it routine is the next step.

**Change.** `scripts/check-config-drift.sh` that diffs every file under `conf/`, `deploy/`, `systemd/` against its installed location, prints the unified diff, and exits nonzero if any file differs. Wire into `systemd/kayak-config-drift.{service,timer}` running Sunday 05:30 (after backup, before maintenance window).

**Acceptance.** Manually edit `/etc/nginx/sites-available/levels-wkcc-org`; next run emits an alert with the diff in `journalctl`.

**Effort.** 0.5 day.

**Depends on.** Nothing.

### T1.3 — Tighten `kayak-pipeline.service` `ReadWritePaths` (also QW.6)

Already enumerated as a Quick Win (QW.6). Listed here so Tier 1 is self-contained.

### T1.4 — Document `@no_transaction` migration recovery

**Why.** Per data-loss audit scenario 14: a `@no_transaction` migration that fails halfway leaves `*_new` tables behind and `schema_migrations` un-bumped. The only such migration today is `0012_reach_name_partial_unique.sql`, but the operator-recovery procedure is not documented anywhere.

**Change.** Add a `## Recovering from a partial @no_transaction migration` section to `docs/operations.md` (created in production-discipline Phase 4.2). Procedure: `.schema | grep '_new'`, drop them, re-check `schema_migrations`, rerun `levels migrate`.

**Effort.** 1 hour.

**Depends on.** production-discipline Phase 4.2 existing.

### T1.5 — Update existing docs for current reality

**Why.** Doc-drift audit found:

- `README.md` lists 5 timers; 8 installed.
- `deploy/SETUP.md §8` lists 6 timers; 8 installed.
- `PLAN_production_discipline.md` Phase 1.3 counts 8 services; 9 are present (`kayak-metadata-snapshot` is the missing one).
- `docs/database-schema.md` is missing the `gauge.river`, `gauge.display_name`, `gauge.sort_name`, `gauge.state` columns plus `source.timezone` plus several indexes; the corresponding SVG was generated 2026-04-10 and has 12 migrations of drift since.
- README.md `levels` Quick Start tells users to install but never activates the venv.
- `docs/offsite-backup.md` restore procedure still references `php8.2-fpm`; prod is `php8.4-fpm`.
- `CHANGELOG.md` last touched 2026-04-22; ~100 commits since.
- 8 PLAN_*.md files, only 1 (`PLAN_c901_cleanup.md`) carries a status banner.

**Change.**

- Update timer/service counts in README, SETUP, PLAN_production_discipline.
- Regenerate `docs/schema-overview.svg` from current `models.py` (one-shot script — `scripts/regenerate_schema_svg.sh` using `eralchemy` or `schemacrawler`; commit + lock cadence to "every migration that touches structural columns").
- Fix README Quick Start: add `source .venv/bin/activate` step, or rewrite using fully-qualified paths.
- Fix `docs/offsite-backup.md` PHP version reference.
- Add status banners (`Status: Done <commit>` / `In progress` / `Drafted` / `Abandoned`) to every `PLAN_*.md`. Move done ones to `docs/done/` (mirroring `docs/one-offs/` pattern).
- Resurrect `CHANGELOG.md` from `git log`-since-last-tag (one-shot extraction) and commit to keeping `[Unreleased]` current going forward.

**Effort.** 1 day total (doc work is mostly mechanical).

**Depends on.** Nothing.

### Tier 1 — completion check

- `kayak-cert-expiry.timer` enabled and healthy for 7 consecutive days.
- `kayak-backup-hourly.timer` enabled; 24-window verified on disk.
- `kayak-config-drift.timer` enabled; one intentional-drift drill triggers the alert.
- `scripts/deploy.sh` exists (per production-discipline Tier 3) and has been used for at least one deploy.
- `docs/operations.md` exists (per production-discipline Phase 4.2) and lists at least the 5 plausible-outage scenarios.
- All 5 doc-drift items in T1.5 resolved.
- Restore drill executed (per production-discipline Phase 4.4) and any discovered drift fed back into `docs/operations.md`.

---

## Tier 2 — Test / CI maturity (~1-2 weeks)

**Objective.** Stop relying on "Pat reads the diff" as a load-bearing gate. Catch regressions in CI that today only show up in prod.

### T2.1 — Pin CI to prod-equivalent PHP & Python (mbstring already addressed in P0.1)

**Why.** P0.1 removes the `mbstring` mismatch. The remaining parity surface: PHP minor version, Python minor version, OS image. CI's `ubuntu-latest` may drift away from Debian 13 over time.

**Change.** In `.github/workflows/ci.yml`:

- Pin `runs-on:` to `ubuntu-24.04` (closer-to-Debian-13 LTS).
- Pin PHP to `8.4.x` explicitly (today: `extensions: pdo_sqlite, curl` with PHP from `shivammathur/setup-php@v2`; freeze to a minor).
- Pin Python to `3.13.x` for the canary job; keep 3.14 in the matrix.
- Document the parity intent in `.github/workflows/ci.yml` header comment.

**Acceptance.** A PHP 8.4.5 → 8.5 upgrade in prod requires a PR that bumps the CI pin; no surprise.

**Effort.** 2 hours.

### T2.2 — Property tests for parsers (Hypothesis)

**Why.** The 6 parsers all handle adversarial-ish text (government data feeds with varying formats). Hand-rolled fixtures cover happy + a few rejection paths; property tests cover the long tail. The calc-expression evaluator (`src/kayak/cli/calculator.py:_safe_eval`) is also a natural target — it's an AST-walking sandbox and property tests can verify "any expression that parses returns a number OR raises ValueError, never escapes the sandbox."

**Change.**

- Add `hypothesis>=6.99` to `[project.optional-dependencies]` `dev` group.
- New `tests/test_parsers/test_<parser>_property.py` per parser; start with `wa_gov` (line-by-line, simplest), then `usbr`, `nwps`, `usace_cda`, `nwrfc_xml`, `nwrfc_textplot`.
- New `tests/test_cli/test_calculator_property.py` covering `_safe_eval` invariants (any rejected input must raise `ValueError`; any accepted input returns finite float or NaN).
- Mark all property tests `@pytest.mark.property`; add to CI as a separate job (so they don't slow the default `pytest -m 'not slow'` path).
- Configure `@settings(derandomize=True, database=None)` so failures are reproducible across CI runs without persisting state.

**Acceptance.** Each parser has ≥1 property test asserting "no crash on `@given(text())`" plus 1-2 parser-specific invariants (positive flow, observed_at within fetch window, etc.). The calc-expression test asserts the sandbox boundary.

**Effort.** 1 day per parser + 0.5 day calc = ~7 days; roll out incrementally over Tier 2.

### T2.3 — Test schema parity check

**Why.** Per DB audit DB-H1: `tests/conftest.py:34` builds the test schema via `Base.metadata.create_all(eng)` — migrations are never exercised. A future migration that drifts from ORM-declared shape passes tests but fails in prod.

**Change.** New `tests/test_db/test_schema_parity.py`:

```python
def test_apply_pending_matches_create_all(tmp_path):
    """A fresh DB built via apply_pending() should match Base.metadata.create_all()."""
    # build via migrations
    db_migrated = tmp_path / "via_migrations.db"
    apply_pending(db_migrated)
    # build via ORM
    db_orm = tmp_path / "via_orm.db"
    Base.metadata.create_all(create_engine(f"sqlite:///{db_orm}"))
    # introspect both and assert equality on table names, columns, indexes, FK shapes
    assert introspect(db_migrated) == introspect(db_orm)
```

The `introspect()` helper queries `sqlite_master` plus `pragma_table_info` / `pragma_index_list` / `pragma_foreign_key_list` for every table, returning a dict that can be deep-compared. Differences cause the assertion to print the precise drift.

**Acceptance.** CI run after a deliberate "migration adds a column but model doesn't" change fails clearly.

**Effort.** 0.5 day.

**Depends on.** Nothing. Blocks T3.5 (dormant schema cleanup) — having the parity test catches the cleanup migrations.

### T2.4 — Replace tautological `test_pipeline.py`

**Why.** Per testing audit TEST-H3: `tests/test_cli/test_pipeline.py:30-52` mocks every step and asserts each was called. This is a tautology; without the existing `test_pipeline_integration.py` (`@pytest.mark.slow`), the pipeline glue has zero behavioral coverage.

**Change.**

- Delete `test_pipeline.py` (or shrink it to one test that asserts "step order is fetch, fetch-usgs-ogc, calc-rating, update-gauge-cache, calculator, build" via inspection, not mocking).
- Extend `test_pipeline_integration.py` to cover all 6 parsers (today: NWPS only). One fixture-driven test per parser, asserting "feed → DB → build output contains the gauge".
- Unmark them `@pytest.mark.slow` if they collectively run under 30 seconds (the current single-parser one runs in ~5s; six should be ~30s and acceptable on default `pytest`).

**Acceptance.** `pytest` (default markers) covers all 6 parsers' wiring end-to-end. The tautological test is gone.

**Effort.** 1 day.

### T2.5 — Playwright editor-journey spec

**Why.** Per testing audit TEST-H4: Playwright covers 5 page loads with no editor-flow coverage. Login → propose → review → approve has zero E2E.

**Change.** `tests/js/editor.spec.ts`: spawn a fresh-DB PHP server (existing pattern in `tests/php/IntegrationTestCase.php`), set `EDITOR_FEATURE=1`, simulate magic-link consumption via DB shortcut (skip the real email path), submit a propose form, switch to a maintainer session, approve, assert `edit_history` row and reach update. Skip in CI if `EDITOR_FEATURE` isn't in scope.

**Acceptance.** Editor regression (e.g. break the CSRF gate) fails CI within the editor.spec.ts run.

**Effort.** 1 day.

### T2.6 — PHPStan in pre-commit

**Why.** Per testing audit: PHPStan runs in CI, not pre-commit. A typed-PHP regression lands and ships before showing up locally.

**Change.** Add to `.pre-commit-config.yaml`:

```yaml
- repo: local
  hooks:
    - id: phpstan
      name: PHPStan
      entry: vendor/bin/phpstan analyse --memory-limit=1G --no-progress
      language: system
      files: \.php$
      pass_filenames: false
```

**Acceptance.** A deliberately-broken PHP file (e.g. call a nonexistent method) fails pre-commit before push.

**Effort.** 15 min.

### T2.7 — PHP coverage gate

**Why.** Per testing audit: `coverage: none` in CI's PHPUnit run means no signal. PHPStan catches some bugs but not "this endpoint was never exercised."

**Change.** Add `pcov` extension (lighter than xdebug for line coverage) to CI's PHP setup; enable `<coverage>` in `phpunit.xml`; add `--coverage-text` summary to the CI step; set a soft floor of 50% on `php/includes/` initially, hard-fail at 40%, ratchet upward.

**Acceptance.** A PR that drops coverage on a covered file under the floor fails CI.

**Effort.** 0.5 day.

### T2.8 — `.gitleaks.toml` + `_<file>_*` convention pre-commit grep

**Why.** Two small hygiene wins. `.gitleaks.toml` lets the gitleaks pre-commit/CI gate allowlist documented test fixtures. The PHP convention grep enforces `_<file>_<func>` per `CLAUDE.md` — today ~60 file-private helpers use bare `_<func>` (audit finding PHP-H3); these grow more collisions over time.

**Change.**

- `.gitleaks.toml` with one allowlist rule (the test PDFs / fixtures that match the entropy heuristic).
- Add `scripts/check-php-helper-prefix.sh` and wire as a `local` pre-commit hook. Logic: for each `function _<name>(` definition in `php/includes/*.php`, assert `<name>` starts with the file's basename (e.g. `_review_handler_*` in `review_handler.php`). Allow the documented `_gp_*` cross-file cluster (per CLAUDE.md).

**Acceptance.** Two pre-commit hooks pass on current code (modulo a one-shot rename pass that's part of T2.8 itself — rename the ~60 offenders in a single PR, then enforce going forward).

**Effort.** 0.5 day for the gitleaks config, 1-2 days for the rename + enforcement (mostly mechanical search-and-replace).

### T2.9 — Move PHP conventions out of `CLAUDE.md`

**Why.** Per doc audit DOC-H4: `CLAUDE.md` is Claude-targeted; a first-time human PHP contributor reading `CONTRIBUTING.md` doesn't see the load-bearing conventions.

**Change.** Extract `CLAUDE.md` §"PHP Conventions (`php/includes/`)" into `php/CONVENTIONS.md`. `CLAUDE.md` keeps a one-line link. `CONTRIBUTING.md` adds a §"PHP code style" pointer.

**Effort.** 30 min.

### Tier 2 — completion check

- `mbstring` removed from CI (P0.1 done).
- CI/prod pinning (T2.1) deployed; one PHP/Python minor-bump PR has gone through cleanly.
- ≥3 parsers have property tests (T2.2 partial); calc-expression has property tests.
- Test schema parity check (T2.3) in place; a deliberate drift PR is caught.
- Tautological pipeline test (T2.4) replaced with 6-parser integration coverage.
- Editor-journey Playwright spec (T2.5) in CI.
- PHPStan in pre-commit (T2.6).
- PHP coverage reporting (T2.7) with at least a 40% hard floor.
- Convention enforcement (T2.8) in pre-commit.
- PHP CONVENTIONS.md (T2.9) extracted.

---

## Tier 3 — Architecture (~2-4 weeks)

**Objective.** Remove the load-bearing assumptions that block growth: containerization, a second host, a second contributor.

### T3.1 — Parser/IO decoupling

**Why.** Per architecture audit ARCH-H1: `src/kayak/parsers/base.py:155-222` does direct DB writes inside `_flush_buffer`. Every parser test must spin up a session. The 6 parsers also vary in their use of `parse_line` vs overriding `parse` outright — the line-by-line abstraction is decaying.

**Change.** Introduce two pure functions:

- `parse_records(text, source) -> Iterator[ObservationRecord]` — pure, returns immutable records.
- `write_records(session, source, records) -> WriteSummary` — the existing `_flush_buffer` logic, now called by the orchestrator not the parser.

Migrate parsers one at a time. Old `BaseParser.parse()` becomes a thin wrapper around `parse_records` + `write_records` for backward compatibility during migration. Eventually `parse_line` is killed entirely.

**Acceptance.** Each parser's test file no longer needs `session`; `parse_records` returns the expected records, asserted with `==` on dataclasses. Halves the test fixtures for parsers.

**Effort.** 2-3 days (~30 min per parser × 6 + base class + tests).

**Depends on.** T2.2 (property tests) lands first so the refactor is safe.

### T3.2 — Pipeline DAG with stage dependencies

**Why.** Per architecture audit ARCH-H2: `cli/pipeline.py:72-86` runs every step regardless of upstream failure. QW.5 is the 5-line stopgap; the real fix is a DAG.

**Change.**

```python
@dataclass
class Step:
    name: str
    fn: Callable
    requires: list[str] = field(default_factory=list)

STEPS = [
    Step("fetch", fetch_cmd),
    Step("fetch-usgs-ogc", fetch_usgs_ogc_cmd, requires=[]),  # independent of fetch
    Step("calc-rating", calc_rating_cmd, requires=["fetch", "fetch-usgs-ogc"]),
    Step("update-gauge-cache", update_gauge_cache_cmd, requires=["calc-rating"]),
    Step("calculator", calculator_cmd, requires=["update-gauge-cache"]),
    Step("build", build_cmd, requires=["update-gauge-cache", "calculator"]),
]

def run_pipeline(args):
    results: dict[str, Result] = {}
    for step in STEPS:
        if any(results[r].failed for r in step.requires if r in results):
            results[step.name] = Result.skipped(reason=f"upstream {step.requires} failed")
            logger.warning("Skipping %s — upstream failed", step.name)
            continue
        try:
            step.fn(args)
            results[step.name] = Result.ok()
        except Exception as e:
            results[step.name] = Result.failed(e)
            logger.exception("Step %s failed", step.name)
    return results
```

**Acceptance.** A test that mocks `fetch` to raise asserts that `build` is not called and a clear `skipped` record exists.

**Effort.** 1 day.

**Depends on.** QW.5 lands first.

### T3.3 — Typed config spine

**Status: Closed (2026-05-15)** — see `docs/done/PLAN_tier3_closeout.md` Phases 0–4 for the per-phase shipping log. Final shape: `src/kayak/config.py` is a `pydantic-settings` model; `levels emit-config` writes `/etc/kayak/runtime-config.json` (mode 0640 root:www-data, atomic same-dir tmp + rename); `php/includes/config.php` reads it via `Config::str/int/bool/list/url()` with no `getenv` fallback (HTTP-500 on missing JSON). The nginx `fastcgi_param` channel for `SQLITE_PATH` / `EDITOR_FEATURE` / `TURNSTILE_SITE_KEY` / `MAIL_FROM` / `SITE_URL` is gone; the FPM pool only re-exports `TURNSTILE_SECRET`. `tests/test_config.py` (Python) + `tests/php/ConfigTest.php` (PHP) enforce schema parity. See `docs/operations.md` § Config for the operator runbook.

**Why.** Per architecture audit ARCH-H7: configuration lives in env, `~/.config/kayak/.env`, `data/sources.yaml`, `data/builder.yaml`, systemd units, nginx `fastcgi_param`, DB tables, and an `EDITOR_FEATURE` runtime flag. Python's `kayak.config` and PHP's `auth_env`/`maintainer_emails` agree on maintainer email only by coincidence (both fall back to a hardcoded string).

**Change.**

- Define a `pydantic-settings`-backed `KayakConfig` model in `src/kayak/config.py` covering env + sourced YAML.
- Add a `levels emit-config` subcommand that writes the resolved config as JSON to `/etc/kayak/runtime-config.json`.
- New `php/includes/config.php` reads that JSON (gated on PHP-FPM startup); replaces ad-hoc env reads.
- DB-table config (`fetch_url`, `calc_expression`) stays — those are *data*, not config.

**Acceptance.** Changing `MAINTAINER_EMAIL` once in env regenerates the JSON via deploy.sh, and both Python + PHP agree on the new value (verified by a small integration test).

**Effort.** 2 days.

**Depends on.** production-discipline Tier 3 (deploy.sh) — the JSON regen step lives there.

### T3.4 — Replace `/home/pat` hardcoding with `KAYAK_HOME`

**Status: Closed (2026-05-15)** — see `docs/done/PLAN_tier3_closeout.md` Phase 5 for the per-sub-phase shipping log. `KAYAK_HOME` lands via `/etc/kayak/env` (installed by `deploy/install-config.sh`); every `kayak-*.service` carries `Environment=KAYAK_HOME=/home/pat` + `EnvironmentFile=-/etc/kayak/env`; every targeted shell script sources `/etc/kayak/env` with a `: "${KAYAK_HOME:=/home/pat}"` fallback prologue. **The acceptance criterion ("grep returns only `KAYAK_HOME=`-style assignments") is consciously unmet.** Three systemd directive shapes can't expand env vars at all (`WorkingDirectory=`, `EnvironmentFile=`, `ReadWritePaths=`) and the `ExecStart=` binary-path slot can't either (systemd 257 rejects `${KAYAK_HOME}/.venv/bin/levels`: "the first argument may not be a variable" — `man systemd.exec`). Two adjacent surfaces (nginx `root /home/pat/public_html;` and PHP-FPM `open_basedir`) similarly can't expand env vars by the layer's design. Each remaining literal carries a leading-comment annotation documenting the layer-level constraint, so a future operator relocating `KAYAK_HOME` sees the reason inline. The Phase 5.7 reconciliation table in the closeout plan breaks the residual 86 hits down line-by-line.

**Why.** Per architecture audit ARCH-H8: `/home/pat` is welded into PHP, systemd, scripts, snapshot. Blocks containerization, blocks any second host, blocks any second maintainer's local dev setup.

**Change.** Introduce `KAYAK_HOME` env var (default `/home/pat`). Replace every literal `/home/pat` in:

- `php/csp-report.php:84`
- `php/includes/db.php` (path fallback)
- `scripts/snapshot_metadata.sh:16`
- `systemd/kayak-heartbeat.sh:18`
- Every `systemd/kayak-*.service`: `WorkingDirectory=`, `EnvironmentFile=`, `ReadWritePaths=`.

Wire `KAYAK_HOME=/home/pat` into `/etc/kayak/env` (sourced from each systemd unit's `EnvironmentFile=`).

**Acceptance.** `grep -rn '/home/pat' --include='*.{php,sh,service,timer}'` returns only `KAYAK_HOME=/home/pat`-style assignments, not bare path literals.

**Effort.** 1 day.

**Depends on.** production-discipline Tier 3 (deploy.sh) — the `KAYAK_HOME` indirection should be in place when deploy.sh lands so it can leverage it.

### T3.5 — Decide on dormant schema features

**Status: Closed (2026-05-15)** — see `data/db/migrations/0022_drop_dormant_features.sql` and `docs/done/PLAN_tier3_closeout.md` § Phase 6 for the per-feature rationale, and `docs/operations.md` § Schema decisions for the audit-vs-reality split.

**Why.** Per architecture audit ARCH-H10: 6+ schema-only features carry maintenance cost on every migration + PHPStan run. Each new migration has to think about them.

**Per-feature decision (as shipped):**

| Feature | Decision | Justification |
|---|---|---|
| `rating` / `rating_data` tables + `calc-rating` step | **KEEP** | Documented dormant in `CLAUDE.md`; reserved for per-gauge rating curves. No active maintenance cost beyond presence. |
| `MaintainerCredential` (WebAuthn schema) | **DROPPED in 0022** | Schema only; no register/assert code. CASCADE on `delete_editor` had nothing to cascade. |
| `ChangeRequestAttachment` (photo uploads) | **KEEP** | Documented as "Phase 2+" pending; FPM upload limit pre-blocks abuse. |
| `ChangeStatus.auto_applied` enum value | **KEEP** | Removing the value shrinks SQLAlchemy-emit `VARCHAR(11)`→`VARCHAR(6)`; the live DB's column is `VARCHAR(11)`. A schema-parity-clean removal needs a table-rebuild migration under `@no_transaction` — cosmetic-only gain. Documented in 0022's commit body. |
| `ChangeTarget.trip_report` enum value | **KEEP** | Same VARCHAR-length reason. |
| `EditorStatus.minimal` tier | **KEEP** | Audit was wrong: `admin.php` promotes `pending→minimal` (first review step), `propose_handler.php` has a `minimal`-specific daily cap (10/day), live DB has 1 editor at this tier. |

**Change (as shipped).** `data/db/migrations/0022_drop_dormant_features.sql` drops `maintainer_credential` only. The three enum-value removals were dropped from scope after the schema-parity test (T2.3) showed that removing them shrinks the SQLAlchemy-emitted VARCHAR length below the live column width; that would require a table-rebuild migration under `@no_transaction` for cosmetic-only gain. `EditorStatus.minimal` was retained outright — the audit's claim of "never authorizes anything" was wrong.

**Acceptance.** Met: `pytest`, `phpstan analyse`, and the T2.3 schema-parity test all pass; `sqlite3 kayak.db .schema | grep maintainer_credential` returns 0 hits.

**Effort.** Actual ~0.5d (one migration + three "decided to keep" commits + this closeout pass). Budget was 1d.

**Depends on.** T2.3 (schema parity test) — landed Phase 3.4.

### T3.6 — Release discipline

**Why.** Today's "what's running in prod" is "whatever main was at the last `cp`." No semver, CHANGELOG dead since 2026-04-22, no release notes.

**Change.**

- Add `scripts/release.sh` that takes a semver bump (patch/minor/major), generates a CHANGELOG entry from `git log --pretty=format:'- %s' <last-tag>..HEAD`, opens `$EDITOR` for the maintainer to clean up, creates the annotated tag, and pushes.
- `scripts/deploy.sh` (production-discipline Tier 3) accepts a tag, not just `main` — deploy operates on tags only.
- `/etc/kayak/VERSION` records the deployed tag (already a production-discipline Tier 3 item).
- CHANGELOG.md `[Unreleased]` is kept current via PR convention — every PR that's user-facing adds an entry.

**Acceptance.** `git tag -l 'v*' | head` shows recent tags; `cat /etc/kayak/VERSION` on prod matches a tag, not a SHA.

**Effort.** 0.5 day for the release script + a one-shot retroactive CHANGELOG fill.

**Depends on.** production-discipline Tier 3 (deploy.sh) — the tag-only deploy enforcement lives there.

### Tier 3 — completion check

- All 6 parsers use the pure `parse_records → write_records` split (T3.1).
- `cli/pipeline.py` is a DAG; a fetch failure cleanly skips downstream (T3.2).
- `levels emit-config` writes `/etc/kayak/runtime-config.json` consumed by both Python and PHP (T3.3).
- `grep -rn '/home/pat' --include='*.{php,sh,service,timer}'` finds only `KAYAK_HOME=` lines (T3.4).
- The schema is one table lighter (T3.5 closed 2026-05-15; the three enum-value removals were dropped from scope — see § T3.5).
- `scripts/release.sh` exists; prod is running a tagged release (T3.6).

---

## Verified clean (do not touch)

These are areas the audit specifically confirmed are right:

- **Dependency hygiene.** `pip-audit --strict` blocks CI; locks fresh (1-23 days); CVE-aware floors.
- **Calc-expression evaluator** (`src/kayak/cli/calculator.py:_safe_eval`). AST-walking sandbox; explicit allowlist; well-tested. Add property tests (T2.2) but don't refactor.
- **SSRF defense** (`src/kayak/utils/http_client.py:_validate_url`). Solid.
- **CSP / security headers.** Strict; `<script>` tags all have `src=`; no inline event handlers (verified by grep).
- **CSRF.** Double-submit cookie + body, `hash_equals` constant-time, rotated on session creation.
- **Session cookies.** HttpOnly + Secure + SameSite=Strict.
- **fail2ban scope.** SSH, nginx, edit/auth — comprehensive (though the propose/comment endpoints are a Medium gap, not High).
- **Magic-link consumption.** Atomic single-use via status-pinned `UPDATE … WHERE used_at IS NULL`.
- **Approval race.** Status-pinned UPDATE prevents double-approve 500s.
- **SQLite ACID.** WAL + `synchronous=NORMAL` + `busy_timeout=30000` + per-source commits. Crash recovery is correct.
- **Online backup.** `sqlite3 .backup` holds only a read lock; backup-during-write is safe.
- **Upload pre-block.** nginx `client_max_body_size 16k` + FPM `post_max_size = 256K` + `upload_max_filesize = 0`. Three-layer block.
- **`Source.name` non-uniqueness.** Documented as intentional (`models.py:161-165`).
- **`ReadWritePaths=/home/pat`** on the pipeline — intentional for the symlink-rename, justified inline. (Tightening is a defense-in-depth Quick Win, not a correctness fix.)

---

## Out of scope (consciously deferred)

These came up in the audit but are not in this plan:

- **WebAuthn passkeys for maintainers.** Would replace magic-link-only for the maintainer tier. The `MaintainerCredential` schema was dropped in migration 0022 (T3.5). Re-add when actually building it.
- **Containerization.** Tier 3.4 (`KAYAK_HOME`) unblocks it, but actually building the Dockerfile and migrating is out of scope. Single-host Hetzner is fine for now.
- **Staging host.** A second host that mirrors prod for testing. Would close the "test/prod parity" loop further than the CI parity in T2.1.
- **Email change for editors.** Per editor-flow audit scenario 13: no endpoint exists today. When built (Phase 2+), must require fresh re-auth + confirmation to the old address.
- **HSTS preload submission.** HSTS is set in nginx; the preload-list submission is a separate manual step.
- **`git filter-repo` to scrub the leaked DreamHost password.** Don't do this — credential is rotated, the rewrite would invalidate every clone, and the credential is already invalid.

---

## Effort summary

| Bucket | Effort | Note |
|---|---|---|
| P0 (pre-release blockers) | 3 hours | mbstring removal + cert monitor |
| Quick Wins | 1 day | 12 small items |
| Tier 1 — Operability | 2-3 weeks | Plus production-discipline.md Tiers 3-4 |
| Tier 2 — Test/CI | 1-2 weeks | Property tests are the long pole |
| Tier 3 — Architecture | 2-4 weeks | Parser refactor + DAG + config spine |

P0 + Quick Wins are the pre-release fix list. Tiers 1-3 are the path to "professional level" — they can be tackled in order (T1 → T2 → T3) or in parallel where dependencies allow.

---

## Reproduce

A subsequent reviewer should re-run these read-only commands against `main` + the live host to confirm the current-state findings remain accurate:

```bash
# Live host
systemctl list-timers --all 'kayak-*'
systemctl list-units --state=loaded 'kayak-*'
ls /etc/letsencrypt/renewal/
cat /etc/letsencrypt/renewal/levels.mousebrains.com.conf | grep authenticator
systemctl status certbot.timer

# Repo
git log --oneline -30
git diff HEAD~5 -- 'src/**/*.py' 'php/**/*.php' | wc -l
ls -la docs/PLAN_*.md
grep -rn 'mb_' src/ php/ scripts/ tests/php/ 2>/dev/null   # expect: empty
grep -rn '/home/pat' --include='*.{php,sh,service,timer}' | wc -l
.venv/bin/pytest -q
vendor/bin/phpunit --no-coverage
vendor/bin/phpstan analyse --memory-limit=1G --no-progress
```

Findings drift relative to plan-date 2026-05-13 should be noted in a new iter-log entry.
