# Plan — Outstanding follow-ups (closeout schedule)

**Status:** Closed 2026-05-15. **Phase 0** (DNS cutover) and **Phase 1**
(T1 closeout: schema SVG regen, timer-count update, archive-done-plans,
stale-vhost cleanup) all landed. **Phase 2** (KAYAK_HOME + deploy.sh +
release.sh) is functionally complete; the shipped scripts diverge from
this plan's prescription in ways driven by feedback memories
(`feedback_no_sudo`, `feedback_sudo_cp_clobbers_overrides`,
`feedback_manual_deploys_ok`) that post-date the 2026-05-14 drafting.
See iter 7 below for the divergence list. **Phase 6** (GHA promotion
+ tag-approval prod deploy) was always conditional and is reopened
under `PLAN_three_instance_layout.md` for post-v1.0.0 work. **Phase
1.4** (restore drill) and **Phase 2.4** (smoke deploy via tagged
release) both await v1.0.0 and are tracked as T+30 follow-ups under
`PLAN_production_discipline.md`'s deferred items.

> **Cross-check:** plan drafted 2026-05-14 against `main` at `af9dab5` —
> after Quick Wins + Tier 1 PRs (T1.1 / T1.2 / T1.4 / T1.5) landed. A
> second Claude session should re-run §Reproduce to confirm the
> open-task inventory before any phase starts.
>
> **Iter log:**
> - iter 1 (2026-05-14): 7 findings — (A) production-discipline Tier 3 is 5 phases (`scripts/deploy.sh` + GHA staging promotion + tag-approval prod deploy + rollback proc + drill), not just deploy.sh; my Phase 2 was undersized by 4-6 days. Split: Phase 2 keeps the local-deploy + KAYAK_HOME + release.sh bundle; the GHA staging promotion + tag-approval pieces move to a new Phase 6 (or Out of scope, pending the staging-host question). (B) Phase 2's deploy.sh design rewritten to match production-discipline §3.1 — `git pull --ff-only` from the working tree, not rsync-from-deploy-clone (simpler and matches today's reality where `/home/pat/kayak` IS the deploy root). (C) Phase 1.3 cross-reference fixup scope expanded: 5 specific files reference moved plans; some cross-boundary (in-repo → `docs/done/`) and some intra-`docs/done/` (within the moved set). Enumerated. (D) Phase 0.2: added explicit `nginx -s reload` and per-vhost verification — certbot `--nginx` may only rewrite one of three vhost files; the other two need manual `ssl_certificate` edits. (E) Phase 1.4 risk wording: the offsite restore could fail because of `gdrive-crypt:` drift; that's the *point* of the drill, but the plan should make the success-vs-gap distinction explicit. (F) Stale `/etc/nginx/sites-available/levels` (15K, May 12) leftover from pre-split — noted as a Phase 1 cleanup tangent. (G) "Phase 2 bundle" rationale strengthened — the real benefit is review cohesion (three commits that read together), not deploy cost.
> - iter 2 (2026-05-14): 8 findings — (A) Phase 2.4 smoke-deploy text still referenced "the rsync diff (should be empty)" left over from iter-0 design; rewritten to match the git-pull-based deploy.sh. (B) deploy.sh script's `git checkout "$TARGET" ; git pull --ff-only || true` was hand-wavy — `git pull` from a detached-HEAD tag is invalid; rewritten so `--head` does `git pull` on the `main` branch while `--tag` just does `git fetch && git checkout <tag>`. (C) deploy.sh drift-check stdout was discarded; piped to a deploy log file and reported back to the operator. (D) Added a "Before you start" subsection to Phase 2 documenting the sudo NOPASSWD prereqs (`systemctl restart kayak-*`, `nginx -s reload`, `tee /etc/kayak/VERSION`) and the `HC_DEPLOY_UUID` healthchecks ID. (E) Phase 0 now references `DNS.CHANGEOVER-fastpath.md` as the source-of-truth doc. (F) Phase 6.3 decision point made explicit: "default = skip; opt-in if a real staging host appears." (G) "Verification end-to-end" was too strict on `editor_security_review` — that plan is menu-style and "Status: Done" doesn't fit; rephrased as "all four plans either Done or explicitly Closed (menu items selected / others deferred)." (H) Dropped the misleading "DB writes are serialized through systemd timers" constraint — they're scheduled, not serialized; WAL handles concurrency. Replaced with a more specific note about backup/restore coordinating with `kayak-pipeline.timer` windows.
> - iter 3 (2026-05-14): 6 findings — (A) deploy.sh's `git diff --name-only HEAD@{1} HEAD` reflog-based "did conf/ change" detection is unreliable: HEAD@{1} doesn't exist on a fresh deploy, and reflog can be garbage-collected. Rewritten to diff against the previous deploy's commit from `/etc/kayak/VERSION` (or unconditionally reload if VERSION is missing/empty). (B) Deploy log path `$KAYAK_HOME/kayak/logs/` needs `mkdir -p` before `tee -a`; without it the first deploy crashes. (C) `set -a; . /etc/kayak/env; set +a` silently no-ops if `/etc/kayak/env` is missing; added an explicit existence check with clear error. (D) `--head` path: `git fetch origin main` then `git pull --ff-only origin main` is redundant — `pull` does fetch. Collapsed. (E) Annotated-tag error message "$TARGET is not an annotated tag" is misleading when the tag simply doesn't exist; differentiated. (F) Phase 3 risk note about `EDITOR_FEATURE=1` was speculative — the editor surface is always-on as of `project_editor_feature` Phase 1+2, so the CI export is unnecessary; rephrased.
> - iter 4 (2026-05-14): 4 findings — (A) deploy.sh's `git checkout main` fails if the working tree has uncommitted changes; added a `git status --porcelain` precheck that refuses to deploy from a dirty tree. (B) `/etc/kayak/env` could supply `KAYAK_HOME=/some/other/path` and the `cd "$KAYAK_HOME/kayak"` would then fail noisily — that's correct behavior (clear blast radius), but worth a sentence in §2.0 "Before you start" so a future reader knows the file is the source of truth, not the script. (C) Phase 1.3 prose had a "wait, …" interjection that read like an unfinished thought; rewritten as a clean bullet. (D) Phase 6.3 effort line said "otherwise blocked" — "blocked" implies external; the default is a conscious skip. Rephrased as "otherwise not started" + cross-link to the explicit Out-of-scope entry.
> - iter 5 (2026-05-14): 4 findings — (A) Phase 2.4 smoke-deploy expected nginx reload to be "skipped" — but the new deploy.sh forces a reload when `$PREV_VERSION` is empty (first deploy = unconditional reload). Corrected the smoke-deploy walkthrough. (B) Phase 6.3 listed "documented rollback procedure" as a *blocker* for Phase 6.3 even though the rollback procedure itself is part of Phase 6.3 (§3.4). Circular. Decoupled: rollback procedure moves into Phase 6.2 as an `operations.md` expansion; Phase 6.3 inherits it. (C) Phase 0.3 missed the `kayak-cert-renewal-test.service` artifact concern — the weekly `certbot renew --dry-run` may leave artifacts in any of the three vhost files; first post-cutover run on Mon should be inspected. Added. (D) Tier 1 completion check (per `PLAN_pre_release_followup.md` §481-489) is not explicitly tracked — the items are split across Phase 0/1/2/6 but the plan never asserts "after these phases, Tier 1 is closed." Added an explicit cross-reference to the end-to-end verification section.
> - iter 6 (2026-05-14, stopping): 3 findings — (A) Effort tally row "Phase 6.1+6.2 — Production-discipline (always)" said ~3.5d but after the iter-5 rollback move it's 1.5d + 2.5d = 4d. Table updated; cumulative columns recomputed. (B) Phase 6 total-effort sentence said "~4-7 days" — same arithmetic gap; bumped to "~4-9 days." (C) `deploy.sh` log path was `$KAYAK_HOME/kayak/logs/` which is *inside* the repo working tree — logs would pollute `git status` and accumulate untracked. Moved to `$KAYAK_HOME/logs/deploy/` (outside the repo). Convergence: 7 → 8 → 6 → 4 → 4 → 3 — stopping.
> - iter 7 (2026-05-15, **closeout audit**): Phase 2 landed between
>   iter 6 and today but the shipped scripts diverge from the plan's
>   prescription. Documenting the gaps so the closed plan stays
>   honest:
>   - **2.1 KAYAK_HOME** — done. The "63 references" audit grew to
>     94 over the past week but ALL remaining `/home/pat` literals
>     are legitimate: shell `${KAYAK_HOME:=/home/pat}` fallbacks,
>     systemd `Environment=KAYAK_HOME=/home/pat` definitions,
>     systemd directive paths (WorkingDirectory=, ExecStart=,
>     ReadWritePaths=) which systemd does NOT variable-expand, and
>     `Config::str('csp_log_path', '/home/pat/logs/csp.log')`
>     defaults. The acceptance criterion "grep returns only
>     KAYAK_HOME-style assignments" is met.
>   - **2.2 `scripts/deploy.sh`** — done with a **more conservative
>     design** than the plan envisioned. Divergences and reasons:
>     (i) no `--tag`/`--head` flag — the script only ever pulls
>     main, because per `project_pr_mode_after_v1` direct-to-main
>     holds until v1.0.0; tag handling is deferred to a follow-up
>     after v1.0.0 ships;
>     (ii) does NOT auto-invoke `sudo systemctl daemon-reload` /
>     `restart kayak-*.timer` / `nginx -s reload` — instead prints
>     a NOTICE listing the changed `systemd/`/`conf/`/`deploy/`
>     paths so the operator applies them manually after diffing.
>     Driven by `feedback_sudo_cp_clobbers_overrides` (repo
>     template can silently clobber prod-tuned values) and
>     `feedback_systemd_in_tree_copy` (paired-edit invariant);
>     (iii) uses `pip install -e .` (only when `pyproject.toml`
>     diffs across the pull) rather than `uv sync --locked
>     --all-extras` every run — pip is what's installed in the
>     prod venv today;
>     (iv) no `HC_DEPLOY_UUID` ping (env var not configured; a
>     deploy event is rare enough that an explicit healthchecks
>     check feels like noise vs signal);
>     (v) no `/etc/kayak/VERSION` write — the plan used VERSION
>     for conf-change detection across deploys, but `deploy.sh`'s
>     "did this pull touch systemd/conf/deploy?" question is
>     answered by `git diff --name-only "$old_sha" "$new_sha"`
>     inside the same invocation, so persistent state isn't
>     needed.
>   - **2.3 `scripts/release.sh`** — done with **different
>     argument shape**: takes a literal `X.Y.Z` instead of
>     `patch|minor|major`. Does NOT create the git tag — only
>     bumps `pyproject.toml`, flips `CHANGELOG.md`'s `[Unreleased]`
>     → `[X.Y.Z] - DATE`, commits, and prints the tag-and-push
>     commands for the operator. Driven by
>     `project_pr_mode_after_v1` — the user controls when the
>     v1.0.0 tag publishes.
>   - **2.4 Smoke deploy** — **gated on v1.0.0**. The first
>     `--tag` deploy was meant to retroactively tag current `main`
>     as `v0.2.0`, but `project_pr_mode_after_v1` reserves the
>     first tag for `v1.0.0`. When v1.0.0 lands, the operator
>     adds the `--tag` flag handling to `deploy.sh`
>     (~10-line patch) and runs the smoke deploy then.
>   - **2.0 Prereqs** — `/etc/kayak/env` exists (✓); sudo NOPASSWD
>     entries unnecessary given divergence (ii); `HC_DEPLOY_UUID`
>     unnecessary given divergence (iv).
>   - **Phase 6** — moved out of this plan entirely to
>     `PLAN_three_instance_layout.md`, which reopens the
>     staging-host question on a non-single-host basis post-v1.0.0.
>   - Plan moved to `docs/done/`; this is the last edit.
>
> Dates absolute. References `file:line` against current `main`.

## Why

Four plans are in flight today:

- `PLAN_pre_release_followup.md` — the 2026-05-13 audit follow-up.
  P0.1, P0.2, all 12 Quick Wins, and Tier 1 PRs (T1.1 / T1.2 / T1.4 /
  T1.5) landed. **Outstanding:** T1.5 leftovers, T1 completion items,
  Tier 2 (test/CI maturity, 9 items), Tier 3 (architecture, 6 items).
- `PLAN_production_discipline.md` — operations roadmap. Phase 1
  (heartbeats + push) landed. **Outstanding:** Tier 2 (status
  visibility), Tier 3 (deploy automation), Tier 4 (runbook + SLO +
  drill). Tier 3 and Tier 4 overlap with the audit plan's Tier 1 and
  T3.6 — this plan deconflicts them.
- `PLAN_dev_env_followups.md` — drafted (iter 5 stopped). **Outstanding:**
  Phase 3 dev-host `OUTPUT_DIR` convention. No prod impact.
- `PLAN_editor_security_review.md` — drafted (iter 9 stopped),
  menu-style. **Outstanding:** Tier 0 threat model gates everything;
  per-tier decisions pending.

Plus the DNS cutover (Phase B / C), scheduled at T0 / T0+3 per user
direction. T0 ≈ 2026-05-21.

This plan sequences that backlog with the DNS cutover as the only
fixed anchor and a single operator working serially.

## Constraints

- **Single operator, serial work.** Sequencing must respect cognitive
  load; no parallel work streams.
- **DNS T0 / T0+3 are immovable.** Other phases flex around them.
- **Live PHP-FPM lacks mbstring** ([reference_php_no_mbstring]). Any
  T2/T3 code added must respect this.
- **`/home/pat` is hardcoded today** — `grep -rn '/home/pat'
  --include='*.php' --include='*.sh' --include='*.service'` returns
  **63 hits** across `php/`, `scripts/`, `systemd/` (audit cited 4-5;
  the real surface is wider). T3.4 KAYAK_HOME must land before
  `scripts/deploy.sh` becomes load-bearing, or deploy.sh has to be
  rewritten when KAYAK_HOME lands later.
- **Backup/restore + deploy timing.** `kayak-pipeline.timer` fires at
  the top of every hour and writes the DB via WAL. Concurrent reads
  are fine, but a `deploy.sh` invocation that restarts the pipeline
  mid-fetch (or a restore that swaps the DB file out from under an
  active write) can lose observations or corrupt state. Run deploys
  and drills in the back half of the hour (`:30-:50`).
- **CI runs `ubuntu-latest`** today; prod is Debian 13. T2.1 pins the
  gap closed; sequencing puts T2.1 first.
- **Bridge cert at `/etc/nginx/certs/levels.wkcc.org.*`** stays valid
  until certbot --expand replaces it. Don't delete prematurely.
- **No sudo in this Claude shell** ([feedback_no_sudo]); deploy /
  systemd / nginx changes are prepared as diffs for the user to apply.
- **Deploy/nginx/systemd changes need per-step confirmation**
  ([feedback_deploy_confirm]); one "go" is not blanket approval.

## Decisions baked in

- **Sequence:** Phase 0 (DNS) || Phase 1 (T1 closeout) → Phase 2
  (deploy + release foundation) → Phase 3 (Tier 2) → Phase 4 (Tier 3)
  → Phase 5 (drafted plans) → Phase 6 (production-discipline closeout).
  Phase 0 runs in parallel with Phase 1 because DNS work is mostly
  waiting and Phase 1 is short.
- **Phase 2 bundles three items** (`T3.4` KAYAK_HOME + `scripts/deploy.sh` +
  `scripts/release.sh`) because they form one cohesive unit — KAYAK_HOME
  is the path indirection deploy.sh consumes; release.sh is the upstream
  half of deploy.sh's tag input. Reading them as three sequential
  commits in one PR is clearer than three independent PRs. The user can
  override and ask for three PRs without breaking the design.
- **Phase 2's deploy.sh follows production-discipline §3.1's design**:
  `git pull --ff-only` from `/home/pat/kayak` (the working tree),
  `uv sync --locked --all-extras`, `levels migrate`, `systemctl
  restart 'kayak-*.timer'`, `levels build`, selective nginx reload.
  This is simpler than a rsync-from-deploy-clone approach and matches
  today's reality.
- **GHA staging promotion + tag-approval prod deploy** (production-discipline
  §3.2 / §3.3) is **deferred to Phase 6**, not bundled into Phase 2.
  Reason: GHA-driven SSH deploys assume a prod-vs-staging host
  separation that doesn't exist today (`levels-test.wkcc.org` is an
  alias on the same Hetzner box). Implementing it now would either
  require provisioning a second host (out of scope) or pretending the
  same-host alias is staging (no value). Defer until a real staging
  host exists or the user explicitly opts in.
- **Tier 2 before Tier 3.** Tier 2 builds the gates that catch Tier 3's
  refactor regressions (T3.1 ↔ T2.2; T3.5 ↔ T2.3).
- **Tier 2's nine items resequence** by dependency rather than audit
  numbering. The audit numbering was descriptive, not prescriptive.
- **Drafted plans (Phase 5) land last.** `dev_env_followups` is
  dev-host-only ergonomics (no prod impact); `editor_security_review`
  requires Tier 0 threat model that hasn't been written. Both
  off-critical-path.
- **`scripts/release.sh` (T3.6) bundles with `scripts/deploy.sh`**
  (Phase 2), not with Tier 3. Reason: release.sh writes a tag,
  deploy.sh consumes a tag — two halves of one pipeline. Building one
  without the other leaves a half-finished system.
- **Restore drill uses an offsite backup** (per
  `PLAN_production_discipline.md` Phase 4.4: "Restore `kayak.db` from
  rclone offsite into a fresh container or temporary VM"), not a
  local backup. The local backups make a successful drill too easy.

## Target shape after this plan executes

- `levels.wkcc.org` (with `-d levels.mousebrains.com -d levels-test.wkcc.org`
  in the same SAN union) served by an LE-managed cert; bridge cert
  retired.
- `kayak-cert-expiry.timer` green for ≥30 consecutive days against the
  3-SAN cert.
- `docs/operations.md` carries all five plausible-outage runbooks (it
  has three today: backup/restore, partial `@no_transaction` recovery,
  pipeline failure triage, config-drift triage) plus SLO definitions
  and recorded drill dates.
- `docs/schema-overview.svg` regenerated from current `models.py`.
- All "Done" PLAN_*.md files in `docs/done/` (`docs/one-offs/` is the
  pattern model — verified to exist at `docs/one-offs/`).
- `scripts/deploy.sh` + `scripts/release.sh` exist; the live host runs
  a tagged version; `/etc/kayak/VERSION` records the tag.
- `grep -rn '/home/pat' --include='*.{php,sh,service,timer}'` returns
  only `KAYAK_HOME=/home/pat`-style assignments.
- Six parsers have Hypothesis property tests; calc-expression sandbox
  has property tests.
- A deliberate "migration adds a column but model doesn't" PR fails
  CI (T2.3 schema parity).
- Editor login → propose → approve has E2E coverage in Playwright (T2.5).
- `phpstan` runs in pre-commit (T2.6); PHP coverage has a ≥40% hard
  floor (T2.7).
- `MaintainerCredential` schema dropped; three enum values pruned
  (T3.5).
- `pydantic-settings`-backed `KayakConfig` is the single source of
  typed runtime config (T3.3).
- Bus-factor partner exists per `PLAN_production_discipline.md` Phase
  4.5.

## Phase 0 — DNS cutover

**Source of truth:** [`DNS.CHANGEOVER-fastpath.md`](../DNS.CHANGEOVER-fastpath.md).
This phase is the execution wrapper around that doc with absolute
dates and verification hooks. Conflicts between the two: the fastpath
doc wins on cutover mechanics; this plan wins on sequencing.

**Anchor:** T0 = scheduled ClubExpress A→CNAME ticket date (per user,
≈ 2026-05-21).

### 0.1 — T0: open ClubExpress ticket

File the support ticket asking ClubExpress to swap the `levels.wkcc.org`
A record for a CNAME to the Kayak Hetzner host (`levels.mousebrains.com`
or the bare A target — confirm in the ticket). Expect 2-3 business days
propagation. Confirm via `dig levels.wkcc.org CNAME @8.8.8.8` from an
off-network resolver.

### 0.2 — T0+3: certbot --expand

With `levels.wkcc.org` resolving to the Kayak host:

```
sudo certbot certonly --nginx --expand \
    -d levels.mousebrains.com \
    -d levels-test.wkcc.org \
    -d levels.wkcc.org
```

Then:

1. Verify all three vhosts point at the new cert. certbot `--nginx`
   typically rewrites `ssl_certificate` directives in the file(s) it
   detects, but with **three separate vhost files** it may only touch
   one. Check each of `/etc/nginx/sites-available/levels-{mousebrains-com,test-wkcc-org,wkcc-org}`
   — they should all reference `/etc/letsencrypt/live/levels.mousebrains.com/`.
   Edit by hand if not.
2. `sudo nginx -t && sudo nginx -s reload`.
3. Retire the bridge cert: `sudo rm /etc/nginx/certs/levels.wkcc.org.*`
   only after step 2 verifies the new cert is serving on
   `levels.wkcc.org`.
4. Mirror the vhost edits into the repo (`conf/sites/levels-wkcc-org`
   → `/etc/letsencrypt/live/levels.mousebrains.com/`); commit. The
   `kayak-config-drift.timer` will otherwise alert next Sunday.

**Smoke:**
```
openssl s_client -connect levels.wkcc.org:443 \
    -servername levels.wkcc.org </dev/null \
  | openssl x509 -noout -ext subjectAltName
```

Expect: SAN list includes all three hostnames.

### 0.3 — T0+7: post-cutover verification

- `kayak-cert-expiry.service` passes with the 3-SAN union.
- Bridge cert files removed; `nginx -T | grep ssl_certificate` shows
  only the LE-managed cert across all three vhosts.
- `levels.wkcc.org`, `levels-test.wkcc.org`, and `levels.mousebrains.com`
  all serve the same content with valid TLS.
- After the first post-cutover Monday `kayak-cert-renewal-test.service`
  run: inspect each of the three vhost files for new certbot artifacts
  (the `--dry-run` plugin sometimes leaves them). Clean up if present;
  `kayak-config-drift.timer` next Sunday will flag any new drift.

**Verification gate (end of Phase 0):** `kayak-cert-expiry.service`
exits 0 against all three SANs for 7 consecutive runs.

### Phase 0 — risks

- ClubExpress ticket may take longer than 3 days. The Phase A
  architecture supports indefinite bridge — both certs coexist; no
  deadline pressure on certbot --expand.

### Phase 0 — effort

0.5 day spread over a week (paperwork + waiting).

## Phase 1 — T1 closeout (parallel with Phase 0)

### 1.1 — `docs/schema-overview.svg` regen

- Install `eralchemy` in `/home/pat/.venv` (`pip install eralchemy`);
  verify graphviz is on the host (`which dot`; install via
  `sudo apt install graphviz` if absent).
- Build `scripts/regenerate_schema_svg.sh`: invoke `eralchemy` against
  `src/kayak/db/models.py`'s SQLAlchemy metadata; emit to
  `docs/schema-overview.svg`. Wrapper script for reproducibility, not
  for automated regeneration.
- Commit the regenerated SVG.
- **Decision:** don't add a pre-commit hook that fails on `models.py`
  changes without an SVG regen — false positives on non-structural
  edits (comment changes, type widenings) outweigh the value.

### 1.2 — Timer counts in `PLAN_production_discipline.md`

The Status banner at the top notes the staleness but the body still
contains "8 services" / "all 8 services" at lines 107, 122 (verified
via grep). Update both inline references; cite the current count (12,
per `ls /etc/systemd/system/kayak-*.timer | wc -l`).

### 1.3 — Archive done plans to `docs/done/`

Per `PLAN_pre_release_followup.md` T1.5 ("Move done ones to `docs/done/`
(mirroring `docs/one-offs/` pattern)"):

- `mkdir docs/done`
- `git mv` the four plans tagged "Status: Done":
  - `docs/PLAN_js_cleanup.md` → `docs/done/PLAN_js_cleanup.md`
  - `docs/PLAN_js_cleanup_phase3.md` → `docs/done/PLAN_js_cleanup_phase3.md`
  - `docs/PLAN_js_smoke_tests.md` → `docs/done/PLAN_js_smoke_tests.md`
  - `docs/PLAN_php_layer_split.md` → `docs/done/PLAN_php_layer_split.md`

**Cross-reference fix-up** (audit via `grep -rn "PLAN_js_cleanup\|
PLAN_js_smoke\|PLAN_php_layer_split" docs/ src/ tests/ php/ scripts/`):

Cross-boundary references (file stays in `docs/`, target moved to
`docs/done/` — needs `done/X.md` prefix):

- `docs/done/PLAN_dev_env_followups.md:242` references `PLAN_php_layer_split.md`
- `docs/done/PLAN_editor_security_review.md:34, 231` references `PLAN_php_layer_split.md`
- `docs/dev-env-followups.md:3` references `PLAN_js_cleanup`

Intra-`docs/done/` references (both files moved → relative paths still
work, no edit needed for plan→plan links inside the moved set):

- `docs/done/PLAN_js_cleanup.md` → `PLAN_js_cleanup_phase3.md`, `PLAN_php_layer_split.md` (both in `docs/done/`; OK)
- `docs/done/PLAN_js_cleanup_phase3.md` → `PLAN_js_cleanup` (in `docs/done/`; OK)
- `docs/done/PLAN_js_smoke_tests.md` → `PLAN_js_cleanup.md` (in `docs/done/`; OK), `PLAN_dev_env_followups.md` (still in `docs/`; rewrite to `../PLAN_dev_env_followups.md`)

Memory (`MEMORY.md`) doesn't reference plan paths directly (it points
to `feedback_*.md` / `reference_*.md` / `project_*.md`). No edit needed
there.

### 1.4 — Restore drill

Per `PLAN_production_discipline.md` Phase 4.4: pull a backup from
`rclone gdrive-crypt:` (not a local backup — the local makes the drill
too easy; the offsite is what matters during host loss). Restore it
into a scratch DB; `PRAGMA integrity_check;`; compare row counts of
`observation` / `reach` / `gauge` against live within an expected
delta. Time the procedure with the runbook in front of you, not from
memory; note every gap; refine `docs/operations.md`.

**A failed drill is a success.** The point is to find gaps — a missing
rclone token, a forgotten passphrase, a stale path in
`docs/offsite-backup.md`, a tool that isn't installed on the recovery
host. Each gap found is written into `docs/operations.md` as a
refinement. The drill is "done" once it can be executed cold from
the runbook without referring to other docs.

### 1.5 — Tangent: clean up stale `/etc/nginx/sites-available/levels`

The three-vhost split (`b20f618`) replaced the monolithic `levels`
config but `/etc/nginx/sites-available/levels` (15 KB, May 12) is
still on disk — it's not symlinked into `sites-enabled/` so nginx
doesn't load it, but `kayak-config-drift.timer` won't flag it
(drift detection only checks repo→`/etc/` direction, not orphans).
Manual `sudo rm` after confirming via `readlink -f
/etc/nginx/sites-enabled/levels*` that nothing references it.

**Verification gate (end of Phase 1):**
- `docs/schema-overview.svg` mtime newer than newest
  `data/db/migrations/*.sql` mtime.
- `git ls-files docs/done/ | wc -l` ≥ 4.
- `grep -nE '8 services|9 services' docs/PLAN_production_discipline.md`
  returns 0 matches.
- `docs/operations.md` records the drill date + outcome.

### Phase 1 — effort

1.5-2 days (eralchemy + graphviz install + drill + doc edits).

### Phase 1 — risks

- `eralchemy` install may pull graphviz Python bindings that fail to
  build on Debian 13; fallback: use `schemacrawler` or hand-edit the
  SVG. Decision deferred to install-time.
- The restore drill from offsite may fail if `gdrive-crypt:` config has
  drifted since `docs/offsite-backup.md` was written. This is exactly
  what the drill is for — log gaps, don't paper over them.

## Phase 2 — Deploy + release foundation (bundled)

**Bundled:** T3.4 (KAYAK_HOME) + production-discipline Tier 3 (deploy.sh)
+ T3.6 (release.sh + tags). Single PR with three commits in this order:

### 2.0 — Before you start

Prereqs the user must arrange (this Claude session can't):

- **sudo NOPASSWD entries** for the `pat` user covering:
  `systemctl daemon-reload`, `systemctl restart kayak-*.timer`,
  `systemctl restart kayak-*.service`, `nginx -t`, `nginx -s reload`,
  `tee /etc/kayak/VERSION`. Without these, `deploy.sh` halts at the
  first sudo prompt.
- **`/etc/kayak/env`** owned by root, mode 640, group `pat`. Holds
  `KAYAK_HOME=/home/pat` plus `HC_DEPLOY_UUID=<uuid>` (new healthchecks
  check for the deploy event). Loaded by `EnvironmentFile=` in each
  unit; sourceable by `deploy.sh` via `set -a; . /etc/kayak/env; set +a`.
  **This file is the source of truth for `KAYAK_HOME`**; changing the
  value here changes every consumer at the next deploy + restart.
- **Healthchecks.io "kayak-deploy" check** created; UUID copied into
  `/etc/kayak/env`.

### 2.1 — Commit 1: `KAYAK_HOME` indirection (T3.4)

Per `PLAN_pre_release_followup.md` §728: introduce `KAYAK_HOME` env
var (default `/home/pat`); replace literal `/home/pat` across all
**63 references** in `php/`, `scripts/`, `systemd/`. The audit cited
4-5 spots; the actual surface is wider — sweep is mandatory.

Specifically:
- `php/csp-report.php:84` + `php/includes/db.php` (path fallback in
  comments — these are doc-only and lower priority but still grep-able)
- `scripts/check-config-drift.sh:21`, `scripts/snapshot_metadata.sh:16-17`
- `scripts/db_push.sh:18-19`, `scripts/db_pull.sh:15-16`
- Every `systemd/kayak-*.service`: `WorkingDirectory=`, `EnvironmentFile=`,
  `ReadWritePaths=`, `ExecStart=` (when it embeds `/home/pat/.venv/bin/...`
  or `/home/pat/kayak/scripts/...`)
- `systemd/kayak-*.sh` shell scripts that embed the path

Wire `KAYAK_HOME=/home/pat` into `/etc/kayak/env` sourced via systemd
`EnvironmentFile=`. The repo's tracked `systemd/kayak-*.service` uses
`EnvironmentFile=${KAYAK_HOME}/.config/kayak/.env` (cleaner) or
`EnvironmentFile=-/etc/kayak/env` then `EnvironmentFile=${HOME}/.config/...`
— pick one consistent pattern.

**Acceptance:** `grep -rn '/home/pat' --include='*.{php,sh,service,timer}'`
returns only `KAYAK_HOME=/home/pat`-style assignments and `~/.config`-style
shell-expansion references (which are user-context, not hardcoded paths).

**Risk:** nginx's `fastcgi_param SQLITE_PATH /home/pat/DB/kayak.db` in
`conf/snippets/levels-common.conf` is **not** auto-rewritten by this
indirection — nginx doesn't evaluate `$KAYAK_HOME`. Two options:
- Keep `SQLITE_PATH` explicit in nginx (clearer; flagged for manual
  edit if KAYAK_HOME ever changes).
- Use `set $kayak_home /home/pat;` in nginx and reference
  `$kayak_home/DB/kayak.db` (less duplication, more fragile).
- **Decision:** keep `SQLITE_PATH` explicit. Document the dependency
  in `conf/snippets/levels-common.conf` comment.

### 2.2 — Commit 2: `scripts/deploy.sh`

Idempotent local deploy that replaces the manual `cp` pattern, following
`PLAN_production_discipline.md` §3.1's design. Operates on the working
tree at `/home/pat/kayak` (no separate deploy clone). Inputs:
`--tag <v0.2.0>` (post-T3.6) or `--head` (dev / pre-tagging).

Steps:

```bash
#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=/etc/kayak/env
[[ -r "$ENV_FILE" ]] || { echo "ERR: $ENV_FILE missing or unreadable"; exit 1; }
set -a; . "$ENV_FILE"; set +a   # loads KAYAK_HOME, HC_DEPLOY_UUID
cd "$KAYAK_HOME/kayak"

mkdir -p "$KAYAK_HOME/logs/deploy"        # outside repo working tree
LOG="$KAYAK_HOME/logs/deploy/$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$LOG") 2>&1

# Remember previous deploy for the conf/ change-detection step
PREV_VERSION="$(cat /etc/kayak/VERSION 2>/dev/null || echo '')"

# Refuse to deploy from a dirty working tree
if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERR: working tree is dirty; commit or stash before deploying"
    git status --short
    exit 1
fi

# 1. Verify input
case "${1:-}" in
    --tag)
        TARGET="$2"
        git fetch --tags
        if ! git rev-parse --verify "refs/tags/$TARGET" >/dev/null 2>&1; then
            echo "ERR: tag $TARGET does not exist"; exit 1
        fi
        if ! git rev-parse "refs/tags/$TARGET^{tag}" >/dev/null 2>&1; then
            echo "ERR: $TARGET is a lightweight tag; use annotated tags only"
            exit 1
        fi
        git checkout "$TARGET"     # detached HEAD; intentional
        ;;
    --head)
        TARGET=main
        git checkout main
        git pull --ff-only origin main
        ;;
    *)
        echo "Usage: $0 --tag <vX.Y.Z> | --head"; exit 1 ;;
esac

# 2. Sync deps (matches CI's `uv sync --locked --all-extras`)
uv sync --locked --all-extras

# 3. Migrate
"$KAYAK_HOME/.venv/bin/levels" migrate

# 4. Drift check (warn, don't fail — drift may be intentional during deploy)
if ! scripts/check-config-drift.sh; then
    echo "WARN: config drift detected; review $LOG before next deploy"
fi

# 5. Restart timers (picks up unit-file changes)
sudo systemctl daemon-reload
sudo systemctl restart 'kayak-*.timer'

# 6. Rebuild static HTML
"$KAYAK_HOME/.venv/bin/levels" build

# 7. Reload nginx only if conf/ changed since previous deploy.
#    PREV_VERSION empty (fresh install) → reload unconditionally.
RELOAD=0
if [[ -z "$PREV_VERSION" ]] || [[ "$PREV_VERSION" == "main" ]]; then
    RELOAD=1
elif git diff --name-only "$PREV_VERSION" HEAD 2>/dev/null | grep -q '^conf/'; then
    RELOAD=1
fi
if [[ "$RELOAD" -eq 1 ]]; then
    sudo nginx -t && sudo nginx -s reload
fi

# 8. Record + ping
echo "$TARGET" | sudo tee /etc/kayak/VERSION >/dev/null
curl -fsS -m 10 --retry 3 "https://hc-ping.com/${HC_DEPLOY_UUID}"
```

Optional `--dry-run`: skip steps 5/6/7/8, print planned actions only.

### 2.3 — Commit 3: `scripts/release.sh` + tag enforcement (T3.6)

- `scripts/release.sh <patch|minor|major>` generates a CHANGELOG entry
  from `git log <last-tag>..HEAD`, opens `$EDITOR`, creates an annotated
  tag, pushes it.
- `scripts/deploy.sh` validates `--tag` matches a real annotated tag
  before proceeding (rejects branches and unsigned tags).
- `/etc/kayak/VERSION` records the deployed tag.

### 2.4 — Smoke deploy

After Phase 2 lands: cut a `v0.2.0` retroactive tag for current `main`
(via `scripts/release.sh patch`); run `scripts/deploy.sh --dry-run
--tag v0.2.0` first; then `scripts/deploy.sh --tag v0.2.0`. The live
host is already on this state — most steps are no-ops:

- `git checkout v0.2.0` switches to the tagged commit (detached HEAD).
- `uv sync` is idempotent.
- `levels migrate` is idempotent.
- `check-config-drift.sh` exits 0 (we just deployed the repo state).
- `systemctl restart 'kayak-*.timer'` restarts cleanly.
- `levels build` regenerates HTML; should be byte-identical or close.
- nginx reload **runs** on this first deploy because `$PREV_VERSION`
  is empty (the conditional treats empty-or-`main` as "reload
  unconditionally"). Subsequent deploys with a known prior tag will
  diff against it and skip reload when `conf/` is untouched.
- `/etc/kayak/VERSION` gets `v0.2.0`.
- Healthchecks ping fires.

Document the procedure in `docs/operations.md` §Deploying.

### Phase 2 — verification gate

- `/etc/kayak/VERSION` = `v0.2.0`.
- `kayak-config-drift.timer` next run: 0 drift.
- `grep -rn '/home/pat' --include='*.{php,sh,service,timer}'`: only
  `KAYAK_HOME=`-style assignments.
- `scripts/deploy.sh --tag <nonexistent>`: exits non-zero with a clear
  error.

### Phase 2 — effort

3-4 days. KAYAK_HOME indirection is ~1 day (mechanical but
broad-touch); deploy.sh ~1 day; release.sh + smoke deploy ~1 day;
testing/iteration ~0.5 day.

### Phase 2 — risks

- **Systemd unit reshuffle:** `EnvironmentFile=` sourcing changes the
  order of variable resolution. Test on the live host carefully — a
  single broken unit can stall the pipeline. Mitigation: deploy unit
  changes one timer at a time, verify each before proceeding.
- **First tagged deploy is empty:** `v0.2.0` is the current `main`, so
  there's no behavioral delta. Good for testing the deploy machinery;
  bad for testing migration logic. Plan a second deploy (`v0.2.1`)
  with a real change to exercise the `levels migrate` codepath.

## Phase 3 — Tier 2 test/CI maturity

Resequenced by dependency rather than audit numbering:

| # | Item | Effort | Notes |
|---|---|---|---|
| 3.1 | T2.1 — CI/prod pin | 2h | No deps. Lands first; raises fidelity of every subsequent CI run. |
| 3.2 | T2.6 — PHPStan in pre-commit | 15m | No deps. Cheapest gate. |
| 3.3 | T2.9 — `php/CONVENTIONS.md` | 30m | No deps. Bundle with T2.6 as one docs-and-tooling PR. |
| 3.4 | T2.3 — Schema parity test | 0.5d | No deps. Blocks T3.5. |
| 3.5 | T2.4 — Replace tautological `test_pipeline.py` | 1d | No deps. Blocks T3.2 indirectly. |
| 3.6 | T2.8 — Gitleaks + `_<file>_*` rename | 1-2d | One PR for the rename pass + pre-commit hook. |
| 3.7 | T2.2 — Hypothesis property tests | 7d | One parser per PR; six parsers + calc; roll out incrementally. Blocks T3.1. |
| 3.8 | T2.5 — Playwright editor-journey spec | 1d | Depends on T2.1 being live. |
| 3.9 | T2.7 — PHP coverage gate | 0.5d | Lands last in Tier 2 so the coverage floor reflects realistic baseline. |

**Verification gate (end of Phase 3):** per `PLAN_pre_release_followup.md`
§631.

**Effort:** ~12 days across Tier 2.

### Phase 3 — risks

- **Hypothesis flakes:** set `@settings(derandomize=True, database=None)`
  to keep runs reproducible. Don't share the Hypothesis database across
  CI runs.
- **PHP coverage tooling:** `pcov` is lighter than `xdebug` but
  requires a CI image rebuild if not already in
  `shivammathur/setup-php@v2`'s default extensions. Acceptance check:
  `setup-php` with `coverage: pcov` works on `ubuntu-24.04` post-T2.1.
- **T2.5 Playwright editor spec** — the editor surface is always-on
  as of `project_editor_feature` Phase 1+2; no special env flag
  needed. The test scaffold spawns a fresh PHP server (per
  `tests/php/IntegrationTestCase.php` pattern) with a seeded editor
  session via `seedEditorSession()`.
- **T2.8 rename pass** could collide with concurrent in-flight PRs;
  schedule when no PHP work is in flight.

## Phase 4 — Tier 3 architecture

Resequenced by dependency:

| # | Item | Effort | Depends on |
|---|---|---|---|
| 4.1 | T3.2 — Pipeline DAG | 1d | QW.5 stopgap (already in) |
| 4.2 | T3.5 — Dormant schema cleanup (closed) | 0d | T2.3 done; see notes |
| 4.3 | T3.3 — Typed config spine | 2d | Phase 2 deploy.sh |
| 4.4 | T3.1 — Parser/IO decoupling | 2-3d | T2.2 property tests for the parser being refactored (Phase 3.7) |

(T3.5: closed 2026-05-15 — see `docs/done/PLAN_tier3_closeout.md` § Phase 6 and migration `data/db/migrations/0022_drop_dormant_features.sql` for the per-feature rationale; the "1d" budget was the audit estimate pre-decision and is no longer needed.)

(`T3.4` and `T3.6` are bundled into Phase 2; not listed here.)

**Verification gate (end of Phase 4):** per `PLAN_pre_release_followup.md`
§788, minus `T3.4` and `T3.6` (already verified in Phase 2 gate).

**Effort:** ~6-7 days.

### Phase 4 — risks

- **T3.5 enum drops are `@no_transaction` migrations.** Run only
  during a maintenance window; recovery procedure already in
  `docs/operations.md` as Cases A/B/C.
- **T3.1 parser refactor changes test fixtures.** Each parser PR has
  to update its tests to use `parse_records` instead of the old
  `parse_line` wrapper. Don't refactor and add property tests in the
  same PR — separate concerns.
- **T3.3 config spine** changes how PHP reads runtime config. Test
  carefully — a broken `php/includes/config.php` breaks every PHP
  page. Mitigation: dual-read for one release (env + JSON, log when
  they disagree).

## Phase 5 — Drafted plans (deferred until Phase 4 completes)

### 5.1 — `PLAN_dev_env_followups.md` Phase 3

**Status (2026-05-14):** done. The OUTPUT_DIR dev convention is documented
in `.env.example:11-23` (full rationale, recommended layout, prod path)
and `CLAUDE.md:22` (one-paragraph note under Local Development Setup
pointing at `.env.example` for full rationale). Live host carries
`OUTPUT_DIR=/home/pat/public_html` in `~/.config/kayak/.env` — already
matches the canonical layout. No code changes needed; remaining
"local-only cleanup on existing dev boxes" steps in
`PLAN_dev_env_followups.md` Phase 3 are reproduce-on-demand and don't
need to commit.

**Effort:** ~1 day → 0 (already shipped via doc edits during the OUTPUT_DIR
convention rollout).

### 5.2 — `PLAN_editor_security_review.md`

**Status (2026-05-14):** all 7 tiers complete from the dev side. The
review produced `docs/security/` (14 documents totalling ~2900 lines)
covering: editor-surface inventory, STRIDE threat model with 29
threats, controls map with file:line refs, findings tracker
(5 Closed, 6 Accepted, 2 Deferred-to-second-maintainer-trigger,
3 Open as operator prod-side confirms), per-tier audit logs, an
incident-response runbook, posture rollup, and decisions log. Plus
three new CLIs (`levels delete-editor`, `levels export-editor`,
`levels editor-retention`) and the editor-retention systemd timer
from Tier 4. Tier-by-tier closeout commits are listed in
`docs/security/README.md` § Tier status; Tier 6 closeout commit is
`1640cc2`.

**Outstanding (operator-side, can't be done from dev):** F-10, F-11,
F-12 prod-side confirms in `docs/security/findings.md` plus the
restore-drill execution (D-T5.3 / Phase 4.4 of production-discipline).

**Effort:** done.

## Phase 6 — Production-discipline closeout

Items from `PLAN_production_discipline.md` not covered in earlier
phases:

### 6.1 — Tier 2 (status visibility)

Partial today via `healthchecks.io` + `journalctl`. Net new:
- Structured logs (drop JSON lines into `journald` `MESSAGE=`)
- A "last 30 days" recap script summarizing alert counts vs. SLO
  targets, sourced from `journalctl -u kayak-*` + healthchecks history.

**Status (2026-05-14):** scaffold + timer shipped.
`kayak.utils.struct_log.emit` writes JSON-envelope events into
journald from the pipeline (`pipeline_start/done`, `step_start/done/
failed/skipped`); `scripts/recap.py` re-parses them via
`journalctl --output=json` and prints a per-step ok/failed/skipped
tally with elapsed_s percentiles. A new `kayak-recap.timer` mails
the operator a 7-day recap every Monday at 07:00 (operator action:
`sudo systemd/install.service.sh` to land the new unit files).
Healthchecks.io history integration is deferred — journald is the
source of truth for our use case.

**Effort:** ~1.5 days → done.

### 6.2 — Tier 4 SLO + bus-factor + rollback proc

- **Rollback procedure** — done 2026-05-14 in `docs/operations.md`
  §Rollback. Documents the SHA-based manual rollback flow (no `deploy.sh
  --tag` until T3.6 release.sh lands), lists six destructive migrations
  that make code-only rollback insufficient, points at backup restore for
  the cross-migration case.
- **SLO definitions** — done 2026-05-14 in `docs/slo.md` (split out
  per production-discipline §4.3). Five targets: availability A
  (≥99.5%/30d), freshness F (≤2h/source), backup RPO B (≤1h), build
  freshness D (≤75min), magic-link delivery E (≥95%). Each row names
  the measurement signal + dashboard. "What's not an SLO" section
  documents the deliberate omissions.
- **Bus-factor partner** — done 2026-05-14 in `docs/operations.md`
  §Bus-factor partner. Documents what the partner needs (access
  matrix), walkthrough cadence (annual), escalation path, and the
  pre-departure checklist. Walkthrough log seeded empty; first entry
  due when the partner is identified.
- **Bus-factor partner** (production-discipline §4.5): identify one
  trusted person; walk through `docs/operations.md` together; document
  escalation path; arrange read-only SSH access.
- **Rollback procedure** (production-discipline §3.4): document in
  `docs/operations.md` §Rollback. Local-deploy variant: `scripts/deploy.sh
  --tag <prev-tag>` (where `<prev-tag>` is the previous `/etc/kayak/VERSION`
  content). Include the "data migrations are forward-only" caveat —
  rolling back code does NOT roll back DB schema; list the migrations
  that can't be reversed.

**Effort:** 1d SLO + ~1d bus-factor + 0.5d rollback proc = ~2.5d.

### 6.3 — GHA staging promotion + tag-approval prod deploy (conditional)

**Default: skip.** Single-host operation (current state) makes the
prod-vs-staging split synthetic. Opt in only if a real staging host is
provisioned, or if the user decides GHA-driven prod deploys are worth
the operational complexity even on a same-host setup.

Production-discipline §3.2 + §3.3. Two pieces, both **conditional on
a real staging host existing**:

- **§3.2 staging promotion:** Push to `main` after CI green triggers
  `deploy-staging.yml` SSHing to the staging host (not `pat` — a
  deploy-only user restricted to `git pull` + `systemctl restart
  kayak-*` + `levels build`). Healthchecks heartbeat on success.
- **§3.3 tag-approval prod:** Tagging `vX.Y.Z` triggers
  `deploy-prod.yml` with GHA environments + protection rules
  requiring manual approval before SSHing to prod.
- **§3.4 rollback procedure:** documented in `docs/operations.md`.
- **§3.5 drill:** push a deliberately-broken change to a feature
  branch; confirm CI catches it, staging is unchanged; push a passing
  change; confirm staging deploys within 5 min, prod is unaffected;
  tag a release; confirm prod requires manual approval.

**Blockers before Phase 6.3 can start:**

- A real second host (Hetzner CPX11?) serving `levels-test.wkcc.org`
  separately from prod, OR a documented decision that "staging" is
  acceptable as a same-host alias (in which case §3.2 collapses into
  §3.3 and only the tag-approval portion is meaningful).
- An SSH-restricted deploy user on prod (and on staging, if separate).
- A documented rollback procedure in `docs/operations.md` — landed in
  Phase 6.2 above; Phase 6.3 only adds the GHA re-run UI alternative.

**Effort:** 3-5 days *if* the staging host exists; otherwise not
started (default per §Out of scope).

### Phase 6 — total effort

~4-9 days (Phase 6.1 + 6.2 always: ~4d; Phase 6.3 conditional: +3-5d
if unblocked).

## Effort tally

| Phase | Effort | Cum |
|---|---|---|
| 0 — DNS | 0.5d (over 1 wk) | 0.5d |
| 1 — T1 closeout | 2d | 2.5d |
| 2 — Deploy/release foundation | 3-4d | 5.5-6.5d |
| 3 — Tier 2 test/CI maturity | ~12d | 17.5-18.5d |
| 4 — Tier 3 architecture | 6-7d | 23.5-25.5d |
| 5 — Drafted plans | 2-3d | 25.5-28.5d |
| 6.1+6.2 — Production-discipline (always) | ~4d | 29.5-32.5d |
| 6.3 — GHA staging/prod (conditional) | 3-5d *if unblocked* | 32.5-37.5d |

Spread over 8-13 weeks at a sustainable solo cadence.

## Verification (end-to-end)

After Phase 6 completes:

- All four upstream plans either show "Status: Done" or "Status:
  Closed" (with menu items explicitly selected for the security
  review; rest deferred with rationale).
- `kayak-cert-expiry.timer` green for ≥30 days against the 3-SAN cert.
- `kayak-config-drift.timer` next run shows 0 drift.
- `docs/operations.md` covers ≥5 outage scenarios with recorded drill
  dates, plus SLO definitions and a rollback procedure.
- `/etc/kayak/VERSION` matches an annotated tag in `git tag -l 'v*'`.
- A deliberate schema drift, a deliberate `var` reintroduction, a
  deliberate broken PHP function — each fails the appropriate gate.

**Tier 1 completion check** (per `PLAN_pre_release_followup.md`
§481-489) is satisfied incrementally across Phase 0 (cert-expiry
green), Phase 1 (drill + doc archival + schema SVG), and Phase 2
(deploy.sh exists and used). After Phase 2 + Phase 6.2's rollback
proc, all bullets in §481-489 are checkable.

## Reproduce

Read-only commands to refresh the inventory before any phase starts:

```bash
# Plan status banners
grep -nH "^\*\*Status:\*\*" docs/PLAN_*.md

# Tier 2/3 items from pre_release_followup
grep -nE "^### T[23]\." docs/done/PLAN_pre_release_followup.md

# Production-discipline phases
grep -nE "^### Tier|^### Phase" docs/PLAN_production_discipline.md

# Current timer count
ls /etc/systemd/system/kayak-*.timer | wc -l

# /home/pat hardcoding surface
grep -rn "/home/pat" --include='*.php' --include='*.sh' \
    --include='*.service' --include='*.timer' \
    php/ scripts/ systemd/ | grep -v "^[^:]*:[0-9]*:#" | wc -l

# Bridge cert still present?
ls -la /etc/nginx/certs/levels.wkcc.org.*

# docs/done exists?
ls docs/done/ 2>&1
```

## Out of scope (consciously deferred)

- **Migration to PostgreSQL or Litestream.** SQLite is fine at current
  scale (single-host, ≤50 RPS); decision deferred until a real load
  problem appears.
- **Multi-region failover.** Single Hetzner host is acceptable;
  doubling infra cost for marginal availability is not justified.
- **Editor UX redesign.** Phase 5.2 covers security; functional UX is
  a separate concern.
- **Container packaging.** T3.4 KAYAK_HOME indirection is a prereq
  for containerization but the actual container build is out of
  scope here. File a separate plan when needed.
- **Mobile app / native client.** Not in this plan's scope at any
  phase.
- **Phase 6.3 (GHA staging promotion + tag-approval prod deploy) by
  default.** Listed as a Phase 6.3 conditional opt-in but the default
  decision is "skip" — current single-host operation makes the
  staging/prod split synthetic. Lift this only after provisioning a
  real staging host.
