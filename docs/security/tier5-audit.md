# Tier 5 — Disclosure + response audit

> Per `docs/PLAN_editor_security_review.md` § Tier 5. Three decision points (disclosure path / IR cadence / re-review cadence) plus two phases (IR runbook draft / backup-restore drill from a security angle).
>
> Cross-references: [decisions.md](decisions.md) for the formal D-T5.x entries; [incident-response.md](incident-response.md) for the runbook produced in Phase 5.4; [tier4-audit.md](tier4-audit.md) Phase 4.5 for the `security.txt` content that lives alongside D-T5.1.

## Scope

What Tier 5 governs:
- How a bug REACHES the operator (D-T5.1).
- What the operator commits to DOING about it once received (D-T5.2).
- How often the whole posture is RE-CHECKED (D-T5.3).
- What the operator DOES during an incident (Phase 5.4 runbook).
- Whether the recovery path actually works (Phase 5.5 drill).

What Tier 5 does NOT govern: vulnerability prevention (Tiers 1–3) or user-data obligations (Tier 4). Tier 5 is the reactive layer.

## Phase 5.1 — Vulnerability disclosure path (D-T5.1)

### Current state

- `static/security.txt` (per D-T4.5) advertises `Contact: mailto:pat.kayak@gmail.com` + `Expires: 2027-05-20T00:00:00Z` + `Preferred-Languages: en`.
- Repo is public at `github.com:mousebrains/kayak_python`; GitHub Security Advisories (GHSA) is available but not enabled.
- Zero historical reports.

### Options

| Option | Description | Activation cost |
|---|---|---|
| A. `security.txt` only (current) | Email-only disclosure to the gmail address | 0; status quo |
| B. + GitHub Security Advisories (GHSA) as a second channel | Coordinated disclosure via GitHub; integrates with the repo's Issues/PRs UI; reporter can be private until publish | ~15 min: enable in repo Settings → Security → "Private vulnerability reporting"; add `Policy: <ghsa-url>` line to security.txt |
| C. HackerOne / Bugcrowd platform | Formal disclosure platform; researcher-facing UX | Hours of policy/scope writing; account setup |
| D. Bug bounty (paid) | $X per finding; researcher attention boost | Significant ongoing cost; only justified if signal-to-noise is concerning |

### Recommendation: **A (security.txt only)**, with GHSA as a documented activation trigger.

Rationale:
1. **Zero historical reports** — the channel isn't a bottleneck.
2. **Gmail address is robust.** Same address backs the seed-maintainer flow; the operator reads it. The IR runbook (Phase 5.4) commits to "best-effort" response (D-T5.2) — promising a faster channel without resourcing it would be dishonest.
3. **GHSA is the natural promotion path.** When a researcher actually reports something coordinated, the operator can enable GHSA in one click as part of the response, and update `security.txt` to add `Policy:` then. Zero pre-investment.
4. **HackerOne / bug bounty** are mismatched to scale.

### Re-evaluation triggers

- First coordinated-disclosure report → consider activating GHSA and adding the `Policy:` line.
- Researcher complains about the email-only path (e.g., wants PGP, or coordinated-publish workflow).
- Disclosure volume hits ≥ 1/quarter (signal: the channel has uptake worth more structure).

## Phase 5.2 — Incident response cadence (D-T5.2)

### Current state

No documented response SLA. The operator is reachable via the same gmail address that backs `security.txt` and the maintainer account.

### Options

| Option | Description |
|---|---|
| A. Best-effort (no commitment) | Honest for a single-operator hobby project |
| B. 24h triage / 7d patch target for high-severity | Aspirational; unenforced |
| C. Real SLA: 4h triage / 48h patch | Requires reliable operator availability; pairs poorly with single-operator |

### Recommendation: **A (Best-effort)**.

Rationale:
1. **Aspirational SLAs that aren't enforced are noise** — they create false expectations on the reporter side and don't change actual response time.
2. **Single-operator means availability is constrained by operator vacation / illness / day-job demands.** A formal SLA that gets routinely missed erodes trust faster than no SLA.
3. **Best-effort is honest** — the IR runbook (Phase 5.4) lists what "best-effort" concretely means: check the security gmail at least daily; respond within reasonable business-hours latency; out-of-hours = next business day.
4. **Reporter expectation-setting** is handled by stating "best-effort response" explicitly in the runbook + (eventually) on the Policy URL if GHSA activates.

### Re-evaluation triggers

- Second maintainer joins → 24h triage becomes credible.
- Site grows beyond hobby scale.
- Compliance regime requires documented SLA.

## Phase 5.3 — Re-review cadence (D-T5.3)

### Current state

This security review is the first formal pass. No documented re-review cadence yet.

### Options

| Option | Description | Effort/year |
|---|---|---|
| A. Once and done | Run this plan, fix findings, walk away | 0 ongoing |
| B. On-major-change-only | New auth flow / new privileged endpoint / new data type triggers a focused re-review | Variable — only when a change qualifies |
| C. Annual full re-review | Calendar entry; ~1 week of work each year | ~1 week/year |

### Recommendation: **B + light annual touch**.

Concretely:
- **Major-change triggers** that require a focused re-review:
  - New PHP endpoint touching the editor pipeline (i.e., joining the 10 endpoints listed in `editor-surface.md`).
  - New DB table containing PII or holding credentials.
  - New external service (auth provider, hosting move, CDN, captcha replacement, etc.).
  - New privileged operation (file upload, bulk-delete, etc.).
  - Major dependency upgrade (PHP-FPM major version, nginx replaced, SQLite → other DB).
  - Each trigger gets a focused review of the affected slice — NOT a re-run of the whole plan.
- **Annual light touch** (every 12 months from this Tier 5 closeout):
  - Re-read `findings.md` and `decisions.md`.
  - For each Active decision with re-evaluation triggers: check whether a trigger has fired since last review.
  - For each Open finding: check whether the underlying code has changed (`git log -- <file>`) in a way that affects the finding.
  - Refresh `static/security.txt` Expires line (per D-T4.5).
  - Update README "Tier status" table if anything's stale.
  - Effort: ~half-day.

Rationale:
1. **A (Once and done) discards the value of the audit infrastructure.** The findings + decisions docs are designed to be checked back against; "once and done" wastes that capacity.
2. **C (Full annual re-review)** is over-investment at hobby scale. Major changes happen rarely; running a full re-audit when nothing changed is busywork.
3. **B alone misses drift detection** — even without major changes, the underlying code drifts, dependencies update, and the threat landscape shifts. The light annual touch is the minimal counter-drift mechanism.

### Re-evaluation triggers

- A "major change" trigger above fires.
- An incident occurs (post-mortem should include re-evaluating any decisions touched by the incident).
- Second maintainer joins (their fresh eyes are themselves a "re-review" event).

## Phase 5.4 — Incident response runbook

**Deliverable: [`incident-response.md`](incident-response.md)** (created in this tier).

Scope confirmed:
- Discovery flows (business-hours / out-of-hours).
- Triage decision tree (severity matrix).
- Containment options: revoke all editor sessions, lock specific editor, take site read-only, take site offline.
- User notification template (single email pattern reusable for any class of incident).
- Hetzner abuse contact + when to escalate.
- Per-credential rotation playbooks (rclone crypt password, Turnstile secret, postfix relay credentials, ssh deploy key, magic-link cookie keying material if any).
- Post-incident review template.

The runbook itself is the artifact. See `incident-response.md`.

## Phase 5.5 — Backup-restore drill (security angle)

**Goal:** Verify that the off-host backup chain (per `docs/offsite-backup.md`) can produce a usable "before-incident" copy of `kayak.db` so that post-incident forensics can diff "what changed" between a known-good snapshot and the live DB.

**Critical constraint:** Per `[feedback_never_overwrite_db]` and `[feedback_never_run_db_push]`:
- This drill MUST NOT restore into `/home/pat/DB/kayak.db` (live).
- This drill MUST NOT use `scripts/db_push.sh` from any host.
- The drill restores into a TEMP path; the operator inspects; nothing crosses into live.

### Drill plan

The actual drill is an operator-side execution; documenting the plan here is the Tier 5 deliverable. The operator can run the drill at their next convenient point (e.g., before each annual light-touch re-review per D-T5.3).

**Step 1 — Pick a target date (the "known-good" snapshot date).**
Choose a backup whose creation timestamp is ≥ 7 days ago. (Adversary may have had access for days; pick before plausible compromise window.)

**Step 2 — Fetch the backup from the offsite store.**
```bash
mkdir -p ~/restore-drill
cd ~/restore-drill
rclone ls gdrive-crypt:                # list available backups
rclone copy gdrive-crypt:kayak-YYYY-MM-DD.db.gz ./
gunzip kayak-YYYY-MM-DD.db.gz
```
Per `docs/offsite-backup.md`: rclone reads `~/.config/rclone/rclone.conf` (chmod 600) which holds the crypt password. If the crypt password is lost, the offsite copy is unrecoverable — this is a Tier 5 dependency, not a Tier 5 drill failure.

**Step 3 — Compute the "what's lost between then and now" gap.**
Compare row counts (and key fields) between the restored snapshot and the live DB. Do NOT compare to live by overwriting; compare via two open sqlite3 connections.
```bash
# Counts on the restored snapshot
sqlite3 ~/restore-drill/kayak-YYYY-MM-DD.db \
  "SELECT 'edit_history', COUNT(*) FROM edit_history;
   SELECT 'change_request', COUNT(*) FROM change_request;
   SELECT 'editor', COUNT(*) FROM editor;
   SELECT 'editor_session_max_id', COALESCE(MAX(id), 0) FROM editor_session;
   SELECT 'observation', COUNT(*) FROM observation;"

# Counts on the live DB (read-only)
sqlite3 -readonly /home/pat/DB/kayak.db \
  "SELECT 'edit_history', COUNT(*) FROM edit_history;
   SELECT 'change_request', COUNT(*) FROM change_request;
   SELECT 'editor', COUNT(*) FROM editor;
   SELECT 'editor_session_max_id', COALESCE(MAX(id), 0) FROM editor_session;
   SELECT 'observation', COUNT(*) FROM observation;"

# Diff
diff <(sqlite3 ~/restore-drill/kayak-... "SELECT id, target_type, target_id, field, changed_at, changed_by FROM edit_history ORDER BY id") \
     <(sqlite3 -readonly /home/pat/DB/kayak.db "SELECT id, target_type, target_id, field, changed_at, changed_by FROM edit_history ORDER BY id")
```
Expected diff: rows that exist in live but not in snapshot are the "gap" — they accumulated after the snapshot was taken. The drill verifies this is what you actually see (no rows missing from live that existed in snapshot — that would indicate live tampering or DB-side delete).

**Step 4 — Verify schema integrity of the restored snapshot.**
```bash
sqlite3 ~/restore-drill/kayak-YYYY-MM-DD.db "PRAGMA integrity_check;"
# Expect: ok
sqlite3 ~/restore-drill/kayak-YYYY-MM-DD.db ".schema editor"
# Compare to current schema; expect possible additions (newer columns from migrations); fewer columns = older snapshot, expected.
```

**Step 5 — Log the drill.**
Append to a per-drill log section in `tier5-audit.md` (this file) under "## Drill log" with: date executed, snapshot date used, gap row counts, any anomalies. Maintain as an append-only record.

### Drill verification gate

The drill is "passing" if:
- Backup pulls cleanly from `gdrive-crypt:` (no encryption-password failure).
- Snapshot DB opens without `PRAGMA integrity_check` errors.
- Row counts on live ≥ row counts on snapshot for append-mostly tables (`edit_history`, `change_request`, `editor`).
- Diff shows only ADDITIONS in live (not deletions / modifications of rows present in snapshot).

If any of those fail, the operator opens an incident per `incident-response.md`.

### Drill log

(append-only — newest entry on top)

_No drills executed yet. First drill triggered by Tier 6 closeout or next annual light-touch re-review per D-T5.3._

## Decision summary (proposed for D-T5.x)

| # | Topic | Recommended option |
|---|---|---|
| D-T5.1 | Vulnerability disclosure path | **A** (security.txt only; GHSA activation deferred to first report trigger) |
| D-T5.2 | IR cadence | **A** (Best-effort; documented in runbook) |
| D-T5.3 | Re-review cadence | **B + light annual touch** (major-change-triggered focused re-review + ~half-day annual housekeeping) |

## New findings

_None. Tier 5 produces a runbook + drill plan rather than code findings._

## Tier 5 verification gate

- [x] D-T5.1, D-T5.2, D-T5.3 documented in [decisions.md](decisions.md).
- [x] Incident-response runbook exists and is concretely actionable: see [incident-response.md](incident-response.md).
- [x] Backup-restore drill plan documented (above). Drill log section initialized; first drill is operator-scheduled.

**Tier 5 status: ✅ Complete.**
