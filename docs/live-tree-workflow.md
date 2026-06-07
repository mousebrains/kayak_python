# Working on the live host: the editable-tree trap

`/home/pat/kayak` is two things at once — the **production runtime** *and* the
**git workspace**. This doc explains why that's a trap, the workaround we use,
and how to recover if you find the live tree in a bad state.

> **Update — data-repo split (Phase 6).** Two things below changed: (1) the nightly
> metadata snapshot (`snapshot_metadata.sh`) now commits/pushes to the **separate
> `kayak_data` repo**, never this code repo — so the code-repo live tree is no
> longer git-*mutated* by any scheduled job, and the "snapshot on the wrong branch"
> risk + its on-`main` guard now concern the `kayak_data` clone, not here. (2) The
> live host's `kayak_python` deploy key is now **read-only** (deploy `git pull`
> only), so you **cannot push from the live host** — worktrees inherit that, so
> branch/PR pushes happen on a **separate dev machine**. To push from the host in a
> pinch, `git -C /home/pat/kayak config --unset core.sshCommand` (reverts to the
> personal key). The worktree discipline below still keeps the live tree on `main`;
> only the *push* leg moved off-host.

## TL;DR

- The venv is an **editable install** (`…/site-packages/_editable_impl_kayak.pth`
  points at `/home/pat/kayak/src`), so the systemd pipeline and scheduled jobs
  run **whatever branch is checked out in `/home/pat/kayak` right now**.
- Therefore `git checkout <feature>` in that tree is an *unannounced deploy*, and
  `git pull` on `main` is *the* deploy.
- **Rule:** keep `/home/pat/kayak` on `main`; do all branch/PR work in a
  worktree; deploy by merging the PR and `git pull`-ing `main`.

  ```bash
  scripts/new-worktree.sh my-feature        # ~/kayak-worktrees/my-feature, off origin/main
  cd ~/kayak-worktrees/my-feature           # edit · commit · push · open the PR here
  # ...merge the PR on GitHub, then deploy:
  cd /home/pat/kayak && git pull --ff-only
  git worktree remove ~/kayak-worktrees/my-feature
  ```

## Why this happens (root cause)

1. `pip install -e ".[dev]"` wrote an editable `.pth` into the venv pointing at
   `/home/pat/kayak/src`, so Python imports `kayak` straight from the working tree.
2. The systemd units (`kayak-pipeline`, `kayak-status`, `kayak-decimate`,
   `kayak-metadata-snapshot`, …) all run `/home/pat/.venv/bin/levels …`, which
   imports from that tree.
3. So the *file on disk* — i.e. the checked-out branch — is the code that runs.
   There is no build/copy step between "edit" and "prod": the working tree **is**
   the deployed artifact.

## What can go wrong (real incidents)

- **The 37-second near-miss (2026-05-25).** A docs branch was checked out in the
  live tree ~37 s before the hourly `kayak-pipeline.service` tick. Had it fired,
  that run would have executed *un-fixed* code (the fix being validated lived on
  another branch). Caught only by switching back in time.
- **`snapshot_metadata.sh` on the wrong branch.** It hardcodes `BRANCH=main` but
  committed on the *checked-out* branch and pushed `origin/main`. On a feature
  branch it would have committed the nightly metadata snapshot off-main and
  silently pushed nothing. Now guarded (see below). *(Pre-split history: the
  snapshot now operates on the separate `kayak_data` clone, not this repo — see
  the update note at the top.)*
- **In general:** every scheduled job silently runs whatever is checked out —
  usually harmless for the read-mostly, idempotent jobs (fetch, build, status),
  but corrupting for the git-mutating one.

## The workaround we use (Option A)

Keep prod and dev in the *same repo*, but never let dev branch state touch the
live checkout:

- **Live tree stays on `main`** — it is the deployed artifact.
- **Branch work happens in a git worktree.** The venv's `.pth` points only at
  `/home/pat/kayak/src`, so a worktree on any branch cannot affect prod:

  ```bash
  scripts/new-worktree.sh <branch>   # creates (or attaches) ~/kayak-worktrees/<branch>
  ```

- **Deploy = merge + pull.** Merge the PR on GitHub, then in the live tree run
  `git pull --ff-only` on `main`. That — and only that — changes what prod runs.
- **Guardrail.** `scripts/snapshot_metadata.sh` refuses to run unless the
  **`kayak_data` clone** is on `main` (bails non-zero → the existing `OnFailure`
  email/ntfy fires), so the one git-mutating job — which now targets `kayak_data`,
  not this repo — can never commit to the wrong branch.

## Recovery: I found the live tree on a feature branch

```bash
cd /home/pat/kayak
git status                       # note any uncommitted edits
git symbolic-ref --short HEAD    # confirm the branch

# Keep uncommitted work by carrying it to a worktree rather than to main:
git stash                        # if there are local edits
git checkout main
git pull --ff-only               # back to the deployed state
scripts/new-worktree.sh resume   # then `git stash pop` inside the worktree if needed
```

Then check whether a scheduled job ran while off-`main`:

```bash
journalctl --unit 'kayak-*' --since '<when it was switched>' --no-pager | less
git log --oneline -5 <the-feature-branch>   # did the snapshot commit land here by mistake?
```

## The deeper fix we deferred (Option B)

Option A is discipline plus one guardrail; nothing *physically* stops a
`git checkout` in the live tree (only the snapshot job bails). The robust fix is
a **frozen install artifact** — a non-editable `pip install .` so prod runs a
copy, immune to working-tree state until an explicit reinstall.

That was a refactor, not a flag, because the runtime read several assets that
live **outside** the package, located via `BASE_DIR` (= repo root, computed from
`__file__`). The dataset-separation work (plan S4a-2) is retiring those one slice
at a time; the table below tracks what is now packaged vs. still repo-root:

| Asset | Where | Resolved by | Frozen-install status |
|---|---|---|---|
| engine defaults (`sources`/`builder`/`descriptions`/`http_concurrency`/`audit_ignore` YAML) + `db/migrations/` | **packaged** under `src/kayak/data/` | `config_data.py`, `cli/migrate.py` via `kayak.resources` (`importlib.resources`) | ✅ resolved by S4a-2 slice A (#125) |
| metadata dataset (the `*.csv` + `reaches*.json`) | external `kayak_data` clone | `METADATA_DIR` (env), not `BASE_DIR` | ✅ not a blocker — external **by design** (club-specific data); a frozen install locates it by env, not working tree |
| `php/` web layer + install templates (`.htaccess`/`404.html`/`robots.txt`) + `LICENSE`/`LICENSE-DATA` | **packaged** under `src/kayak/web/{php,install-templates,legal}/` | `web/build/deploy.py` via `kayak.resources` | ✅ resolved by S4a-2 slice B2 |
| committed `static/` assets (map.js, leaflet, images, manifest, sw.js, …) | **packaged** under `src/kayak/web/static/` | `web/build/deploy.py`, `_shared.py` via the packaged dir | ✅ resolved by S4a-2 slice B1 |
| generated OSMB GeoJSON (`osmb-*.geojson`) | OSMB staging dir (`OSMB_DIR`; default `BASE_DIR/var/osmb`) | `cli/fetch_osmb.py` writes; `deploy.py` copies into `OUTPUT_DIR/static` | ✅ not a blocker — env-located generated runtime data, like `output_dir` |
| `Gauge-metadata-cache/`, `docs/regression/` (incl. published regression HTML) | repo root | gauges build, regression render | build-time inputs, not import-time — deferred to S3 / lower priority |

`src/kayak/web/static/style.css` was already package-relative. With slices A + B1
+ B2 done, the engine's Python-side defaults, schema migrations, committed web
static assets, the PHP layer, install templates, and `LICENSE` files all survive
a non-editable `pip install .` as-is (the repo-root `LICENSE`/`LICENSE-DATA` stay
for GitHub/pyproject; a test guards the packaged copies against drift); the
metadata dataset and generated OSMB GeoJSON are intentionally env-located
(`METADATA_DIR` / `OSMB_DIR`) rather than working-tree-relative. The only
remaining `BASE_DIR` call sites are the build/cache inputs above
(`Gauge-metadata-cache/`, `docs/regression/`) — deferred to S3 as build-time
reads, not import-time blockers. The PHP layer needs no special handling beyond
the relocation, since `levels build` only copies it into the output docroot. A
true frozen (non-editable) `pip install .` is now viable for everything except
those build-time inputs, and the `wheel-smoke` CI job (`scripts/wheel-smoke.sh`,
S4a-2 slice C) continuously verifies it — it builds the wheel, installs it into a
fresh venv outside the checkout, and runs `init-db` + `build` against the
packaged resources, so a regression to a repo-root `BASE_DIR` read fails CI.
Flipping prod from the editable `.pth` install to a frozen artifact remains a
separate, optional follow-up.

---

*Related:* `CLAUDE.md` § "Working on the live host" (the short version), and
`docs/PLAN_production_discipline.md` (the recurring "discipline at the seams"
theme that this is an instance of).
