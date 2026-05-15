# Plan — Three-instance host layout (prod / test / tpw)

**Status:** Skeleton drafted 2026-05-15 against `main` at `c1e9789` —
post-v1.0.0-release work, scheduled to start around T+30 (≈ 2026-06-21
if v1.0.0 tags on 2026-05-21). This is an **iteratable skeleton**, not
a finished plan. Sections marked **TBD** are intentional placeholders
to be filled in across the next ~month of iter passes.

> **Cross-check:** plan drafted 2026-05-15. Sequel to
> `docs/done/PLAN_outstanding_followups.md` § 6.3 ("GHA staging
> promotion + tag-approval prod deploy"), which was deferred there
> because "single-host operation makes the prod/staging split
> synthetic." This plan reopens 6.3 on the explicit decision that
> same-host **with proper instance isolation** is meaningful enough to
> justify the work — paired with the user's atomicity requirement.
>
> **Iter log:** (none yet — iter 0 = this draft)

## Why

After v1.0.0 the workflow shifts from "direct-to-`main` on a single
host" to "test-then-promote." The user-visible motivation is **atomicity**:
when a deploy is in progress, no human visiting `levels.wkcc.org` should
ever see a half-rendered page, an old HTML referencing a new asset
hash, a broken `description.php` because the DB migration is half-way,
or a 500 from a code-vs-schema mismatch. The current `scripts/deploy.sh`
does a `git pull` + `levels build` + `systemctl restart` sequence in
the live working tree — there are several seconds where the publicly
served state is mid-transition.

The second motivation is **multiple developers**. Going from solo to
bus-factor-2 (or more) requires a workflow where dev A's experiment
doesn't risk prod, and where promotion to prod is an explicit
human-gated action that survives the introduction of a second pair of
hands.

The third motivation is the **`levels.mousebrains.com` independence**
question. After v1.0.0 the WKCC takes ownership of `levels.wkcc.org`;
`levels.mousebrains.com` becomes "Pat's thing" with its own lifecycle.
What that means architecturally is a **decision point** (see § Decisions
deferred).

## Constraints

- **Single physical host.** Hetzner CPX21 (current). No second VPS in
  scope; staging "host" is a logical instance on the same machine.
- **Single SQLite DB per instance.** Concurrency is solved within an
  instance (WAL + busy_timeout); separate instances get separate DB
  files.
- **No GHA-driven SSH-into-host deploys** (per `PLAN_outstanding_followups`
  §6.3 deferral rationale). Deploys are local: the operator (or bus-
  factor partner) SSHes to the host and runs `scripts/deploy.sh`.
- **`KAYAK_HOME` indirection already exists** (T3.4). This plan extends
  it from one value to per-instance values via systemd template units.
- **Multiple developers, low cadence.** ≤3 people, pushes-per-week not
  pushes-per-hour. Optimize for clarity over throughput.
- **Atomic from the user's perspective.** Half-done deploy states must
  not be visible on `levels.wkcc.org`. Backend transitions (timer
  restarts, FPM reloads) are OK to be brief.
- **`feedback_no_sudo`** — Claude prepares diffs; the operator applies
  systemd / nginx / FPM changes.

## Decisions baked in

- **Three full instances**, not three docroots. Each instance owns its
  own `kayak/` clone, `.venv`, `DB/`, releases dir, public docroot,
  systemd templated services, FPM pool, env file. Sharing any of these
  across instances defeats the isolation that makes staging meaningful.
- **Test instance: snapshot-from-prod, not live fetch.** A nightly
  `sqlite3 .backup` from prod's DB lands at test's DB; test runs
  `levels build` only. Pipeline fetch on test is manually triggerable
  (the systemd timer for `kayak-pipeline@test` is `Unit=… DefaultDependencies=no`
  / disabled by default) for developer-driven parser validation.
- **Releases-symlink atomicity (capistrano-style).** Each instance
  layout:
  ```
  ~/<instance>/
    releases/
      v1.0.0/   kayak/ .venv/ public_html/      # built from this tag
      v1.0.1/   kayak/ .venv/ public_html/      # next release
    current  -> releases/v1.0.1                  # atomic symlink swap
    DB/      kayak.db (+ WAL sidecars)           # shared across releases
    logs/    instance-local logs                 # shared
  ```
  nginx serves from `~/<instance>/current/public_html`; PHP-FPM chroots
  at `~/<instance>/current/`. Deploy is "build the new release dir,
  then `ln -sfn` the `current` symlink" — one rename(2) syscall, atomic
  from the kernel's view.
- **DB stays at `~/<instance>/DB/kayak.db`** (not in `current/`).
  Migrations must follow expand-then-contract so the OLD code can read
  the NEW schema (forward-compat) and the rollback path doesn't break.
  This is the one unavoidable "DB transition is briefly visible"
  window; expand-then-contract is how Rails / Django / etc. solve it.
- **Only `origin/<ref>` is deployable.** `scripts/deploy.sh` clones
  fresh from `origin` into the new release dir — never deploys from
  the host's local working tree. Stops "I forgot to push" / "the host
  has a stale `git pull`" failure modes cold.
- **`main` HEAD auto-flows to test; annotated tags to prod.** Test
  instance picks up `origin/main` on a daily timer (or on-demand
  `scripts/deploy.sh --instance test --head`). Prod accepts only
  `--tag vX.Y.Z` (annotated tags, signed-or-not is a TBD).
- **Branch protection on `main` once dev #2 joins.** PRs required;
  one reviewer minimum.

## Decisions deferred (TBD — for iteration)

These are open. Each section below proposes options; iteration picks
one.

### D-1: What does `levels.mousebrains.com` independence mean?

Three options, ordered cheapest → heaviest:

- **(a) Vhost-only split, shared data.** `~/tpw/` is a thin instance
  with its own docroot but `current` is a symlink to `~/prod/current/`.
  Cosmetic separation only (different favicon, different `<title>`,
  different About page). DB is prod's DB. Cheapest; doesn't really
  achieve "independence."
- **(b) Frozen archive.** `~/tpw/` is a snapshot of prod as of v1.0.0
  (HTML + DB), never updated. `levels.mousebrains.com` becomes a
  historical reference. Easiest "true independence"; no maintenance.
- **(c) Independent live instance.** `~/tpw/` is a full instance with
  its own pipeline, its own DB, its own deploy cadence. Pat maintains
  it as his own project; WKCC has no say. Most flexibility, most
  ongoing cost.

Open until a Pat decision; the plan can be executed for prod + test
without resolving this if `tpw` stays on the current "vhost alias on
prod data" footing during Phase 1-5, then Phase 6 handles whichever
option lands.

### D-2: Test-instance fetch policy beyond snapshot

Snapshot covers "build-time changes" but not parser/calc-expression
changes. Options for the latter:

- **(a) Pure snapshot, parsers tested only on developer machines.** Dev
  runs `levels fetch --parser <name>` locally against their own DB.
  Cheapest.
- **(b) Snapshot + manual-trigger fetch.** Snapshot daily; allow
  `systemctl start kayak-pipeline@test.service` (or `levels pipeline
  --instance test`) when a developer wants to validate fetch behavior.
  Test DB then drifts from prod until next snapshot.
- **(c) Snapshot + parallel fetch on a once-a-day cadence.** Test
  doubles upstream load on a slower cadence than prod. Most thorough,
  most rate-limit risk.

Iter-1 default: **(b)**.

### D-3: How does "CI green on `main`" trigger a test-instance deploy?

- **(a) Polling cron on the host.** Every N minutes, `scripts/deploy-
  test-from-main.sh` checks the latest `origin/main` SHA against the
  GitHub commit-status API; if green and newer than current, deploy.
- **(b) Webhook + nginx endpoint.** GitHub fires a webhook to a tiny
  PHP endpoint on the host; the endpoint enqueues a deploy.
- **(c) Manual only.** Developer SSHes in and runs `scripts/deploy.sh
  --instance test --head` after every PR they care about seeing on
  test. Cheapest and most explicit.

Iter-1 default: **(c)**. (a) and (b) are upgrade paths once the
multi-dev cadence warrants them.

### D-4: Migration handling across the prod-snapshot → test boundary

When prod is at schema version N and test snapshots prod's DB, but
test's code is on `main` which contains migration N+1 — the snapshot
arrives at version N and test boots its code expecting N+1. Either:

- **(a) Snapshot script runs `levels migrate` on the test DB after
  copy.** Forward-only; if N+1 is destructive, the snapshot's data
  reshape is irreversible (but it's a snapshot, so that's fine).
- **(b) Test code must be "schema N OR N+1 tolerant" via the expand-
  then-contract pattern.** Real but expensive discipline.

Iter-1 default: **(a)** — snapshot is regenerated daily, no need for
test code to tolerate prior schemas.

### D-5: Tag signing on prod deploys

- **(a) Annotated tags only**, no signing required. Simplest.
- **(b) GPG-signed annotated tags**, `deploy.sh --tag` rejects
  unsigned. Stronger supply-chain story; needs key management for each
  dev who tags.

Iter-1 default: **(a)**. Revisit if the dev set grows past 2.

## Target shape (after this plan executes)

```
/home/pat/
├── prod/
│   ├── releases/
│   │   ├── v1.0.0/   (kayak/ .venv/ public_html/)
│   │   ├── v1.0.1/   ...
│   │   └── v1.0.2/   ...
│   ├── current -> releases/v1.0.2
│   ├── DB/kayak.db (+ WAL sidecars)
│   └── logs/
├── test/
│   ├── releases/
│   │   ├── 2026-05-21-abc1234/   (per-deploy, not per-tag)
│   │   ├── 2026-05-22-def5678/
│   │   └── 2026-05-23-fed4321/
│   ├── current -> releases/2026-05-23-fed4321
│   ├── DB/kayak.db (refreshed nightly from prod snapshot)
│   └── logs/
└── tpw/                   # shape depends on D-1
    └── …
```

systemd:
- `kayak-pipeline@prod.timer` + `.service` — current cadence (hourly).
- `kayak-pipeline@test.timer` — disabled by default; manual trigger.
- `kayak-pipeline@tpw.timer` — depends on D-1.
- `kayak-snapshot-prod-to-test.timer` — nightly (03:00 UTC?), reads
  prod DB via `sqlite3 .backup`, writes to test DB, runs
  `levels build --instance test`.
- All existing `kayak-*` units become `kayak-*@<instance>` template
  units, parameterized by `EnvironmentFile=/etc/kayak/%i.env`.

nginx:
- Three vhost files (already split per `b20f618`) each pointing at
  `/home/pat/<instance>/current/public_html` for `root` and
  `<instance>` for FPM-pool `fastcgi_pass`.

PHP-FPM:
- Three pools (`/etc/php/8.4/fpm/pool.d/levels-{prod,test,tpw}.conf`),
  each with its own `chroot`, `open_basedir`, `SQLITE_PATH`, env vars,
  user (all `pat` for now; consider a `pat-test` user as a future
  hardening step).

Deploy:
- `scripts/deploy.sh --instance <name> --tag <vX.Y.Z>` (prod) or
  `--instance test --head` (test).
- Clones `origin/<ref>` into `~/<instance>/releases/<id>/kayak/`.
- `uv sync --locked --all-extras` into `<release-dir>/.venv`.
- `levels migrate --instance <name>` (against the instance's DB).
- `levels build --instance <name>` into `<release-dir>/public_html/`.
- `ln -sfn <release-dir> ~/<instance>/current` — the atomic moment.
- Reload PHP-FPM pool (graceful — drains in-flight requests).
- Conditional nginx reload if `conf/` changed.
- Cleanup: keep last N releases (default N=5); prune older.

## Migration phases

Six phases. Order matters: each phase leaves the host in a working
state; rollback is "revert the last phase's commit."

### Phase 1 — Define the instance contract (drafting only, no host changes)

**Output:** repo changes only.
- New `systemd/kayak-pipeline@.service` template (parameterized by
  `%i` = instance name).
- New `/etc/kayak/<instance>.env` schema documented in
  `docs/operations.md`.
- New `scripts/deploy.sh` rewrite supporting `--instance` arg.
- New `scripts/snapshot-prod-to-test.sh`.
- New `systemd/kayak-snapshot-prod-to-test.{service,timer}`.
- Tests: `tests/test_scripts/test_deploy_instance.py` (unit-level —
  parses args, validates instance name, doesn't execute).

**Verification:** repo green; nothing deployed yet.

### Phase 2 — Carve prod off into `~/prod/`

The hot move. Today: `~/public_html` is prod's docroot, `~/DB/kayak.db`
is prod's DB. Phase 2 relocates these into `~/prod/current/public_html`
and `~/prod/DB/kayak.db` without dropping a request.

**Sketch (TBD detail):**
- Build `~/prod/releases/v1.0.0/` from the current main + sync deps.
- Atomically swap nginx + FPM to point at `~/prod/current/`.
- Move DB: `sqlite3 ~/DB/kayak.db .backup ~/prod/DB/kayak.db`; reload
  PHP-FPM; verify; only then remove the old `~/DB/kayak.db`.
- Update each `kayak-*@prod` unit's `EnvironmentFile` to
  `/etc/kayak/prod.env` carrying the new paths.

**Risk:** highest of any phase. Mitigation: dry-run on the test
instance once it exists (chicken-and-egg with Phase 3 — see
Decisions deferred).

**Verification:** `curl https://levels.wkcc.org/index.html` returns
the same bytes pre/post (modulo timestamp).

### Phase 3 — Build the test instance (`~/test/`)

**Sketch (TBD):**
- `mkdir -p ~/test/{releases,DB,logs}`
- Initial DB seed: `sqlite3 ~/prod/DB/kayak.db .backup ~/test/DB/kayak.db`
- Configure `/etc/kayak/test.env` (different `OUTPUT_DIR`, different
  `SITE_URL=https://levels-test.wkcc.org`, etc.).
- Install `kayak-pipeline@test.timer` **disabled by default**.
- Install `kayak-snapshot-prod-to-test.timer`.
- Configure new FPM pool `levels-test.conf`.
- Configure nginx vhost `levels-test.wkcc.org` to route at the new pool
  and `~/test/current/`.

**Verification:** `curl https://levels-test.wkcc.org/index.html`
returns content built from yesterday's prod data; `curl
https://levels.wkcc.org/index.html` unchanged.

### Phase 4 — Atomic deploy hardening (the user-experience commitment)

This phase is where the **atomic-from-user-perspective** invariant
locks in. Phase 2/3's deploy mechanic worked but may have had brief
windows of inconsistency.

**Sketch (TBD detail):**
- Refactor `scripts/deploy.sh` to use the releases-symlink pattern
  throughout. Each deploy creates a NEW release dir; the swap is a
  single `ln -sfn`.
- Pre-swap validation step: `levels build` must complete inside the
  new release dir before swap. If build fails, the live `current` is
  untouched.
- Migration handling: `levels migrate` runs against the instance's
  shared DB BEFORE the symlink swap (so the new code sees the new
  schema after swap). Migrations follow expand-then-contract — the old
  code (still serving in-flight requests via the symlink-as-was) must
  tolerate the new schema for the brief overlap window.
- Asset hash invariant: HTML in `<release-dir>/public_html/` references
  `style-<hash>.css` etc. Hashed asset URLs guarantee browsers never
  see a half-state across the swap.
- Rollback: `ln -sfn ~/prod/releases/<previous> ~/prod/current` — same
  atomic move backward. (DB rollback is separate; see Risks.)

**Verification:** ad-hoc race-test — deploy a change that swaps
`index.html`'s `<title>`; while the deploy is running, hammer
`/index.html` with `wrk -t4 -c100 -d10s` from another shell; assert
every response carries either the old title or the new title, never a
partial / broken response. Expect: zero broken responses.

### Phase 5 — Promotion workflow + CI-green gate

**Sketch (TBD):**
- `scripts/deploy.sh --tag <v>` checks the GitHub commit-status API for
  green CI on that tag's commit before proceeding. Refuse to deploy a
  red commit even with `--force`.
- Document the promotion workflow in `docs/operations.md` § Deploying:
  - PR merges to `main` → operator runs `scripts/deploy.sh --instance
    test --head` → smoke-test on `levels-test.wkcc.org` → cut tag via
    `scripts/release.sh` → `scripts/deploy.sh --instance prod --tag
    <v>`.
- Branch protection on `main` (GitHub web UI): require PR + 1 review
  once the dev set is ≥2.

**Verification:** intentionally push a red commit; try `deploy.sh
--instance prod --tag <red-tag>`; expect refusal.

### Phase 6 — `levels.mousebrains.com` independence (depends on D-1)

Implementation depends on D-1's resolution:
- D-1(a): `~/tpw/current -> ~/prod/current/`; one nginx vhost change.
- D-1(b): `cp -r ~/prod/releases/v1.0.0 ~/tpw/releases/v1.0.0; ln -sfn
  ~/tpw/releases/v1.0.0 ~/tpw/current; sqlite3 .backup` once.
- D-1(c): full instance build mirroring Phase 3, with its own pipeline
  timer enabled.

**Verification:** `curl https://levels.mousebrains.com/index.html`
returns whatever D-1 says it should.

### Phase 7 — Multi-developer onboarding

**Sketch:**
- Document in `docs/operations.md` § Roles + access matrix:
  - **operator (Pat):** all instances, all systemd, all sudo
  - **bus-factor partner:** ssh + `scripts/deploy.sh` on prod/test;
    no sudo for FPM/nginx config (per
    `docs/done/PLAN_editor_security_review.md` D-T1.3)
  - **dev contributor:** PR access only; no host access until
    promoted to bus-factor partner
- Wire the access map into systemd / sudo / nginx as appropriate.
- Walkthrough cadence inherited from
  `docs/done/PLAN_pre_release_followup.md` § 6.2.

**Verification:** the bus-factor partner runs through a full deploy +
rollback with the runbook in front of them and notes every gap.

## Risks

- **DB migration is the one unavoidable non-atomic window.** SQLite
  schema changes are visible to the running app while in progress.
  Mitigation: expand-then-contract discipline; migrations under
  `@no_transaction` for schema rewrites; runbook in
  `docs/operations.md` for the recovery cases.
- **PHP-FPM pool reload during deploy.** Brief window where some
  in-flight requests are on old code, new ones on new. PHP-FPM's
  reload is graceful (drains workers); this is normally fine but
  consider load-shedding during deploys if a user-visible glitch is
  observed.
- **Releases dir disk creep.** N=5 keeps things bounded; document
  retention in operations.md.
- **Symlink-aware tooling.** `git status` inside `~/prod/current/kayak`
  may behave oddly because `current` is a symlink. Document that all
  git operations on the host should `cd ~/prod/releases/<current>/kayak`
  or rely on `scripts/deploy.sh` exclusively.
- **Bus-factor partner accidentally deploys main HEAD to prod.**
  `scripts/deploy.sh --instance prod` refuses `--head`; only `--tag`
  is accepted. Hard refusal, not warning.
- **Multi-instance systemd template gotchas.** `kayak-pipeline@test`
  inheriting `OnCalendar=` from the template means changing prod's
  cadence accidentally changes test's. Use drop-in overrides
  (`/etc/systemd/system/kayak-pipeline@test.timer.d/`) for per-
  instance differences.
- **Snapshot can mask a real prod problem.** If prod's DB is in a bad
  state, the snapshot perpetuates it on test. Worth a pre-snapshot
  `PRAGMA integrity_check` (cheap; runs in seconds).
- **`levels.mousebrains.com` decision creep.** D-1 is open; if it
  drifts into Phase 6 unresolved, that phase will balloon. Resolve
  D-1 before starting Phase 6.

## Out of scope

- **Provisioning a second VPS.** Phase 6.3 of
  `docs/done/PLAN_outstanding_followups.md` was about same-host
  staging because adding a host doubles infra cost for marginal
  isolation. Revisit if/when budget supports it.
- **Postgres / migration off SQLite.** Separate plan if it ever
  happens. Out of scope here.
- **Containerization / Docker.** Adjacent: `KAYAK_HOME` indirection
  unblocks it, but the actual container build is its own work.
- **GHA-driven prod deploys.** Was Phase 6.3 of `PLAN_outstanding_
  followups`; superseded by this plan's local-deploy + tag-gating.
- **Real CDN for static assets.** Nginx + content-hashed asset URLs
  are sufficient at this scale.
- **Per-instance SSL certificates.** All three vhosts share the 3-SAN
  Let's Encrypt cert (per Phase 0 of `PLAN_outstanding_followups`).
  Doesn't change with this plan.

## Reproduce

Read-only commands to refresh state before any phase starts:

```bash
# Current docroot + DB layout
ls -la ~/public_html ~/DB ~/.config/kayak 2>&1 | head
readlink -f ~/public_html ~/DB

# Current systemd unit inventory
systemctl list-units --all --type=service 'kayak-*' --no-legend \
  | awk '{print $1}'
systemctl list-timers --all 'kayak-*' --no-legend | awk '{print $NF}'

# Current nginx vhost roots
sudo nginx -T 2>/dev/null | grep -E '^\s+root|server_name' | sed 's/^\s\+//'

# Current FPM pool config
ls /etc/php/8.4/fpm/pool.d/

# Current deploy.sh shape
sed -n '1,30p' scripts/deploy.sh

# Disk free (releases dirs will eat some)
df -h /home/pat
```

## Open work / next iteration

What this skeleton **doesn't** have yet, in roughly the order it'd be
useful to add:

1. **Iter-1 pass:** resolve D-1 (`tpw` interpretation) so Phase 6 can
   be sized. Also resolve D-2 / D-3 / D-4 / D-5 defaults if they need
   to change.
2. **Iter-2 pass:** flesh out Phase 2's cutover sequence step-by-step
   (this is the highest-risk phase; needs a runbook-style walkthrough,
   not bullet points).
3. **Iter-3 pass:** write the templated systemd unit shape and
   verify it actually expands correctly on the live host (chicken-and-
   egg with Phase 1: the unit is in the repo but not installed until
   Phase 2).
4. **Iter-4 pass:** atomic-deploy race-test design (Phase 4) — what
   exactly does "wrk hammering during deploy" measure, and what's the
   pass/fail bar?
5. **Iter-5 pass:** PHP-FPM pool config diffs for the three pools.
   `open_basedir` per pool, `chroot` per pool, `SQLITE_PATH` per pool.
6. **Iter-N pass:** keep iterating until convergence per the project
   convention (≤2 findings per pass, run until 0).

Iter passes update the iter log at the top of this file.
