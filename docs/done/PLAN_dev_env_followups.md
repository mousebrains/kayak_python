# Plan — Dev-environment follow-ups

**Status:** Closed (verified clean 2026-05-15 against `main` at `a559fc0`). All three phases shipped:

- **Phase 1 — Lint config cleanup:** `biome.json` no longer references
  `php/style.css`; `Makefile`'s `lint-css` target is
  `biome check src/kayak/web/static/style.css`; `lint-shell` is
  `shellcheck --severity=warning scripts/*.sh systemd/*.sh deploy/*.sh`
  (no `hardening/*.sh` glob); `.gitignore` carries the five new
  stray-artifact patterns (`static/levels.js`, `filters.js`,
  `sparklines.json`, `style-*.css`, `style.css.hash`) and the stale
  `php/style.css` entry is gone; `php/style.css` itself is gone from
  disk.
- **Phase 2 — PHP doc-root fix:** `php/includes/header.php`'s
  `css_head_block()` uses
  `$doc_root = $_SERVER['DOCUMENT_ROOT'] ?? (__DIR__ . '/..')` with
  the same explanatory comment Phase 2 proposed, aligning it with
  `gauge_map.php:79-88`'s precedent.
- **Phase 3 — `OUTPUT_DIR` dev convention:** `.env.example` carries
  the multi-paragraph DEFAULT / RECOMMENDED / Production rationale;
  `CLAUDE.md` § Local Development Setup has the OUTPUT_DIR convention
  paragraph (at line 22 of the current file).

**Residual:** the post-Phase-3 local-only `rm` cleanups (any leftover
`php/style.css`, `public_html/csp-report.php`, etc. from pre-convention
dev boxes) are operator-side and per-host — out of scope for any
repo commit. The live host needs none of them.

> **Cross-check:** plan drafted 2026-05-12 against `main` at `9446e51`; iter 1 re-verified against `21c9e1a`; iter 2 against `b141f79`; iter 3 against `2c5a4e4`. Inputs (`biome.json`, `Makefile`, `.gitignore`, `php/includes/header.php`, `php/includes/gauge_map.php`, `deploy/SETUP.md`, `src/kayak/config.py`, `src/kayak/web/build/deploy.py`, `src/kayak/web/build/_shared.py`, `.env.example`) unchanged across all ranges; the only commits in range touch `docs/security/*.md` and this plan itself. A reviewer on a default-config dev machine (`OUTPUT_DIR` unset → defaults to `BASE_DIR/public_html` per `src/kayak/config.py:35`) will see additional symptoms; the fixes themselves are env-independent.
>
> **Iter log:**
> - iter 1 (2026-05-12): structural pivot — Phase 3 changes from "drop the symlinks" / "extend the symlinks" to "tell dev boxes to use a non-repo `OUTPUT_DIR`" (per user input). Old A/B/C alternatives kept as an appendix. Smaller fixes: cross-check ref bump, count corrections, end-state phrasing, `.env.example` callout.
> - iter 2 (2026-05-12): 6 findings — Phase 3 cleanup script made safe for `public_html/includes` directory→symlink replacement; Phase 2 verification gate split into before/after-fix scenarios per layout; redundant Phase 2 risk text deduplicated; `.env.example` diff now uses a concrete commented example value; out-of-scope adds an nginx-dev ACL note; iter log + cross-check ref updated.
> - iter 3 (2026-05-12): 3 findings — Phase 1 now also drops the stale `.gitignore:41` `php/style.css` entry (user-flagged); confirmed via grep that no build code writes to `php/style.css` — `deploy.py:107` and `_shared.py:52` only touch `output_dir/style.css`. Phase 3 verification gate corrected: `OUTPUT_DIR` is read by python-dotenv via `kayak.config`, not by shell `source` of the env file (bare `KEY=VAL` lines aren't exported into the child process). Cross-check ref updated.
> - iter 4 (2026-05-12): 2 findings — End-state bullet about `php/style.css` "gitignored anyway" now contradicts iter 3 (gitignore entry is removed); rewritten to describe the post-fix invariant ("file gone AND gitignore entry gone"). Reproduce-section grep for `.gitignore` now also confirms the line removal.
> - iter 5 (2026-05-12, this revision, stopping): 2 findings — Phase 1 Risk parenthetical "(already in `.gitignore:41`)" was misleading post-iter-3 (Phase 1 §4 removes that entry); rephrased to clarify the actual invariant (file is unstaged by §5 before any commit). Phase 3 verification path `/path/to/public_html_dev` → `$OUTPUT_DIR` for consistency with the rest of the section. Convergence: 9 → 6 → 3 → 2 → 2 findings.
>
> Dates absolute. Citations `file:line` against current `main`.

## Context

The original catalogue (committed as `9446e51` under `docs/dev-env-followups.md`, removed once this plan closed) listed five pre-existing dev-environment issues found during JS cleanup. The doc was written from a dev machine where the build target collides with the repo (`OUTPUT_DIR` unset); on the live host the symptoms are silent but the underlying inconsistencies remain. This plan converts the catalogue into a phased fix:

- **Phase 1 — Lint config cleanup (Issues 1, 2; partial 4):** drop stale `php/style.css` and `hardening/*.sh` refs, add gitignore patterns for stray `static/` build artifacts. Env-independent; trivial.
- **Phase 2 — PHP doc-root fix (Issue 3):** narrow `css_head_block()` change so a symlinked-dev-server view of the site renders the styled nav bar. One PHP line; existing precedent at `php/includes/gauge_map.php:88`. Defensive after Phase 3 (see below) but worth doing in its own right.
- **Phase 3 — `OUTPUT_DIR` dev convention (Issues 4, 5):** document that dev boxes set `OUTPUT_DIR` to a non-repo path (mirroring the live host's `~/.config/kayak/.env` which already sets `OUTPUT_DIR=/home/pat/public_html`). Repo's `public_html/` symlinks stay as a dev convenience; the build no longer collides with them. One-time cleanup of any pre-convention stragglers from existing dev boxes. The legacy "drop / extend the symlinks" decision (Options A/B/C from the original draft) becomes an appendix.

Standalone `biome` / `shellcheck` CLI install was scoped out per user direction (2026-05-12) — pre-commit + CI remain the verification surface; `make lint-*` targets are non-runnable on the live host until those tools are installed locally.

## Why

The fixes are all small but heterogeneous:

- **Lint hygiene (1, 2):** `make lint-css` silently checks 1 file instead of 2 (biome `internalError/io` exit 0); `make lint-shell` halts on a glob that bash passes through literally. Both surfaces are unused on the live host but cost developer trust on any machine that does run them — and CI's `biome check` log carries the same `No such file or directory` warning.
- **PHP doc-root (3):** if a developer hosts the repo directly (`php -S localhost:8000 -t public_html`) the symlinked `public_html/includes/header.php` resolves `__DIR__/..` to `/home/pat/kayak/php`, looking for `static/style.css.hash` and `style.css` in the wrong place. Net effect: empty `<style></style>` block, no styled nav, no popup positioning. The codebase already uses `$_SERVER['DOCUMENT_ROOT']` for static-asset mtimes (12 occurrences across 8 files; see `php/includes/gauge_map.php:79-88` for the explanatory comment). `css_head_block()` is the only outlier.
- **`public_html/` hygiene (4, 5):** 24 PHP files are tracked as `120000` symlinks; 6 newer entry points (`csp-report.php`, `custom_gauges.php`, `gauge_picker.php`, `sw.js`, `sitemap.xml`, `style.css`) are not tracked at all. The asymmetry is a symptom: if `OUTPUT_DIR` collides with the repo on dev, the build clobbers symlinks and drops the 6 newer files alongside; if `OUTPUT_DIR` is a sibling, neither happens. Live already operates the latter way. The cheapest fix is to make dev do the same.

Goal: tree is clean on every host; `make lint-*` runs to completion where the tools exist; serving the repo via `php -S` produces a fully-styled page; the `public_html/` symlink set works as a dev convenience without forcing a tracked-asset decision.

## Current state (verified on `levels`)

| Item | State | Env-dependent? |
|---|---|---|
| `php/style.css` on disk | exists (9706 B, mtime 2026-04-11; gitignored at `.gitignore:41`) | yes — depends on whether the dev box ever rebuilt before commit `a4e1e02` |
| `biome.json:15` | references `"php/style.css"` | no |
| `Makefile:35` | `biome check php/style.css src/kayak/web/static/style.css` | no |
| `Makefile:38` | globs `hardening/*.sh` (dir doesn't exist) | no |
| `deploy/install-config.sh` | exists; not lint-covered | no |
| `public_html/includes` (repo) | symlink → `../php/includes` (tracked, mode 120000) | no — same on both hosts |
| `public_html/includes` (deploy target `/home/pat/public_html/includes`) | real directory | yes — on default-config dev the deploy target == repo |
| `css_head_block()` at `php/includes/header.php:30` | `$doc_root = __DIR__ . '/..'` | yes — only a problem on dev hosts that serve the repo's `public_html/` directly via `php -S` |
| `$_SERVER['DOCUMENT_ROOT']` precedent | 12 occurrences across 8 files (`custom.php`, `custom_gauges.php`, `description.php`, `gauge.php`, `gauge_picker.php`, `picker.php`, `reach.php`, `includes/gauge_map.php`) | n/a |
| `public_html/*.php` tracked | 24 mode-120000 symlinks | no |
| `public_html/*.php` untracked | 6 entry points: `csp-report.php`, `custom_gauges.php`, `gauge_picker.php`, `sw.js`, `sitemap.xml`, `style.css` | no |
| stray `static/` duplicates (`levels.js`, `filters.js`, `sparklines.json`, `style-*.css`, `style.css.hash`) | absent on this host | yes — only appear if a default-config build ran here |
| `.env.example:12` | `# OUTPUT_DIR=/home/pat/public_html` (commented; comment hints "default: public_html/ in repo") | no |
| `deploy/SETUP.md:633` | template shows `OUTPUT_DIR=/home/pat/public_html` (prod-style) | no |
| `~/.config/kayak/.env` (live host) | `OUTPUT_DIR=/home/pat/public_html` set | yes — live only |

## Phase 1 — Lint config cleanup (1 commit, ~5 minutes)

Three small edits + one local-only cleanup. Commits to `main`; pre-commit + CI verify.

1. **`biome.json:15`** — remove the `"php/style.css"` entry from `files.includes`. Resulting `lint-css` set is 1 CSS file (`src/kayak/web/static/style.css`); biome no longer emits `internalError/io`.
2. **`Makefile:35` (`lint-css` target)** — change to `biome check src/kayak/web/static/style.css`. (Drop `php/style.css`.)
3. **`Makefile:38` (`lint-shell` target)** — drop the `hardening/*.sh` glob. Add `deploy/*.sh` (1 file present: `deploy/install-config.sh`). Resulting target: `shellcheck --severity=warning scripts/*.sh systemd/*.sh deploy/*.sh`.
4. **`.gitignore`** —
   - **Remove** the stale `php/style.css` entry at `.gitignore:41`. No build code writes to that path (verified: `deploy.py:107` and `_shared.py:52` only target `output_dir/style.css`); the entry has been masking the on-disk leftover from `a4e1e02` rather than gating any active artifact.
   - **Add** stray build-artifact patterns to forestall Issue 4's reappearance on any dev box that still uses default `OUTPUT_DIR`:
     ```
     static/levels.js
     static/filters.js
     static/sparklines.json
     static/style-*.css
     static/style.css.hash
     ```
     (`static/reaches-geom.json` / `static/reaches-state.json` already present at `.gitignore:39-40`.)
5. **Local-only cleanup (don't commit):** on any host where `php/style.css` exists as a leftover, `rm php/style.css`. After the §4 gitignore removal, the file is no longer shielded — `git status` will surface it on the next run if it's still on disk. Removing it pre-emptively keeps `git status` quiet. On a dev box that ran a default-config build before adopting Phase 3's convention, also `rm` any stragglers from §4's added gitignore patterns.

**Verification gate:**
- `pre-commit run --all-files` clean (biome + shellcheck both fire via pre-commit on this host — verified 2026-05-12).
- `git status` shows only the intended diff (one biome.json edit + two Makefile lines + .gitignore additions).
- On a machine with standalone tools: `make lint-css`, `make lint-shell`, `make lint-all` all run to completion. (Not testable on `levels` until biome/shellcheck CLIs are installed; tracked separately.)

**Risk:** the `rm php/style.css` step (§5) is local-only and per-host: an unrelated dev box may not have the file, in which case the `rm` is a no-op. The Phase 1 commit itself doesn't stage `php/style.css` — the commit's diff is the biome/Makefile/`.gitignore` edits only — so even after §4 removes the gitignore entry, a leftover `php/style.css` won't sneak into the commit. It would show as untracked in `git status` from that point forward, which is what §5's `rm` resolves.

## Phase 2 — PHP doc-root fix (1 commit, ~10 minutes)

One narrow edit to `css_head_block()` so symlinked-dev-server views (`php -S -t public_html`) render the styled nav. After Phase 3, the canonical dev workflow uses a non-repo `OUTPUT_DIR` and doesn't trigger the bug — but the fix is still worth landing because:

- The PHP-built-in-server workflow remains valid for anyone debugging without a rebuild loop.
- The `$_SERVER['DOCUMENT_ROOT']` form is already the documented convention (12 sites; the comment at `gauge_map.php:79-88` explains why `__DIR__/..` is wrong for docroot-relative lookups). `css_head_block()` is the only docroot-relative consumer that doesn't follow it.

Other `__DIR__/..` sites in `php/` are `require_once __DIR__ . '/includes/X.php'` style — sibling-source-file loads, which work correctly through the symlink because both ends are inside the `php/` source tree. Those don't need to change.

**The change** (`php/includes/header.php:27-45`): replace `__DIR__ . '/..'` with `$_SERVER['DOCUMENT_ROOT']`, falling back to `__DIR__ . '/..'` only if the server variable is unset (CLI / misconfigured FPM).

Before:
```php
function css_head_block(): string {
    static $block = null;
    if ($block !== null) return $block;
    $doc_root = __DIR__ . '/..';
    $hash_path = $doc_root . '/static/style.css.hash';
    if (is_readable($hash_path)) {
        $hash = trim((string)file_get_contents($hash_path));
        if ($hash !== '' && is_readable("$doc_root/static/style-$hash.css")) {
            $block = '<link rel="stylesheet" href="/static/style-'
                   . htmlspecialchars($hash) . '.css">';
            return $block;
        }
    }
    $path = $doc_root . '/style.css';
    $css = is_readable($path) ? (string)file_get_contents($path) : '';
    $block = "<style>\n$css\n</style>";
    return $block;
}
```

After:
```php
function css_head_block(): string {
    static $block = null;
    if ($block !== null) return $block;
    // Use the request-time doc root rather than __DIR__/.. — when this file
    // is loaded through the symlink at public_html/includes/header.php (dev
    // workflow), __DIR__ resolves to php/includes (the symlink target) and
    // __DIR__/.. resolves to php/, which lacks the hashed CSS sidecar. Same
    // pattern as leaflet.css in gauge_map.php:79-88.
    $doc_root = $_SERVER['DOCUMENT_ROOT'] ?? (__DIR__ . '/..');
    $hash_path = $doc_root . '/static/style.css.hash';
    if (is_readable($hash_path)) {
        $hash = trim((string)file_get_contents($hash_path));
        if ($hash !== '' && is_readable("$doc_root/static/style-$hash.css")) {
            $block = '<link rel="stylesheet" href="/static/style-'
                   . htmlspecialchars($hash) . '.css">';
            return $block;
        }
    }
    $path = $doc_root . '/style.css';
    $css = is_readable($path) ? (string)file_get_contents($path) : '';
    $block = "<style>\n$css\n</style>";
    return $block;
}
```

The `?? (__DIR__ . '/..')` fallback covers CLI invocations (where `$_SERVER['DOCUMENT_ROOT']` is unset) — currently a non-case since this function is never reached from CLI, but the fallback costs nothing and keeps the function defensible if a CLI tool ever needs to render a page.

**Verification gate:**

| Layout | Before fix | After fix |
|---|---|---|
| Live (`OUTPUT_DIR=/home/pat/public_html`, served by nginx + FPM) | `<link rel="stylesheet" href="/static/style-<hash>.css">` rendered — `__DIR__/..` resolves to `/home/pat/public_html`, hashed sidecar present, hash branch taken | Same — `$_SERVER['DOCUMENT_ROOT']` is `/home/pat/public_html`, same hash branch taken, indistinguishable output. Verified via `curl -s https://levels.mousebrains.com/description.php?id=1 \| grep '<link rel="stylesheet"'`. |
| Dev with default `OUTPUT_DIR` (build wrote to repo's `public_html/`), served by `php -S localhost:8000 -t public_html` | Empty `<style></style>` block — `__DIR__/..` realpath-resolves through the `public_html/includes` symlink to `/home/pat/kayak/php`, where neither `static/style.css.hash` nor `style.css` exist (or `php/style.css` falls through to a stale inline blob if the Phase 1 leftover hasn't been deleted) | Styled nav renders — `$_SERVER['DOCUMENT_ROOT']` is the realpath of `-t public_html` (`/home/pat/kayak/public_html`), hashed sidecar present at `public_html/static/style.css.hash`, hash branch taken. |
| Dev with Phase 3's `OUTPUT_DIR=$HOME/public_html_dev`, served by `php -S … -t $OUTPUT_DIR` | Styled nav renders (the dev target is a real directory with no symlinks — `__DIR__/..` works fine pre-fix) | Same — regression check that the fix didn't break the working path. |

**Risk:** the `?? (__DIR__ . '/..')` fallback inverts priority — pre-fix code always used `__DIR__/..`; post-fix uses `DOCUMENT_ROOT` first. The only environments where `DOCUMENT_ROOT` is empty/missing are PHP-CLI and badly-configured FPM. On `levels` the FPM config sets it; verified by the existing `gauge_map.php` site working correctly. If a future FPM pool drops it, both this function and the 12 sibling `$_SERVER['DOCUMENT_ROOT']` users would break the same way — surfacing immediately via the curl smoke test.

## Phase 3 — `OUTPUT_DIR` dev convention (1 commit, ~10 minutes)

**Recommendation.** Dev boxes set `OUTPUT_DIR` to a non-repo path (e.g. `$HOME/public_html_dev`) in `~/.config/kayak/.env` — mirroring how the live host already operates (`OUTPUT_DIR=/home/pat/public_html`, outside the repo at `/home/pat/kayak`). Build writes there; PHP serves from there with `php -S localhost:8000 -t $OUTPUT_DIR`. Repo's `public_html/` is never touched by builds, so the 24 tracked symlinks stay clean and the 6 untracked entry points stop mattering (the build creates them fresh in `public_html_dev` every run).

This is "dev does what prod does, just at a different path." Zero repo code changes. Solves Issues 4 (future recurrences) and 5 entirely; solves Issue 3 for any dev who follows the convention. Phase 2's PHP fix remains useful for devs who deviate (e.g. `php -S -t public_html` against the repo directly).

**Implementation:**

1. **`.env.example:11-12`** — tighten the existing commented hint to be explicit about the dev convention:
   ```diff
   -# Output directory for generated HTML/CSV/text (default: public_html/ in repo)
   -# OUTPUT_DIR=/home/pat/public_html
   +# Output directory for generated HTML/CSV/text.
   +#
   +# DEFAULT (unset): BASE_DIR/public_html — collides with the repo's
   +# public_html/ tracked symlinks and may drop stray build artifacts into
   +# static/. NOT recommended for active development.
   +#
   +# RECOMMENDED on dev boxes: set to a non-repo path (commit-clean):
   +#   OUTPUT_DIR=/home/<user>/public_html_dev
   +# Then: levels build && php -S localhost:8000 -t /home/<user>/public_html_dev
   +#
   +# Production sets this to the live nginx docroot (see deploy/SETUP.md:633):
   +#   OUTPUT_DIR=/home/pat/public_html
   ```
2. **`CLAUDE.md` — Local Development Setup section** — add an entry to the path table (or a one-paragraph note adjacent to it) calling out the dev `OUTPUT_DIR` convention so a fresh `git clone` user finds it immediately. Suggested phrasing: "Dev workflow: set `OUTPUT_DIR=/home/<user>/public_html_dev` so `levels build` writes outside the repo (matches the production layout). Default behavior writes into the repo's `public_html/` and clobbers the dev symlinks; see `.env.example` for the full rationale."
3. **Local-only cleanup on existing dev boxes (don't commit):** after adopting the new `OUTPUT_DIR`, restore the repo with:
   ```bash
   # 6 newer entry points that a default-config build wrote alongside the
   # tracked symlinks — none of them are tracked, so just rm:
   rm -f public_html/csp-report.php public_html/custom_gauges.php \
         public_html/gauge_picker.php public_html/sw.js \
         public_html/sitemap.xml public_html/style.css

   # public_html/includes: tracked as a symlink, but a default-config build
   # made it a real directory full of files. `git checkout` won't replace a
   # populated directory with a symlink; rm -rf first.
   rm -rf public_html/includes
   git checkout public_html/includes

   # 24 tracked PHP symlinks: `git checkout` cleanly replaces regular files
   # with the symlink mode from the index.
   git checkout public_html/*.php

   # static/ stragglers (Phase 1 §5):
   rm -f static/levels.js static/filters.js static/sparklines.json \
         static/style-*.css static/style.css.hash
   ```

**Verification gate:**
- `OUTPUT_DIR` is read by `python-dotenv` via `kayak.config` (not by shell `source` — bare `KEY=VAL` lines wouldn't export to a child process anyway). To verify the env file picks up correctly: `/home/pat/.venv/bin/python -c 'from kayak import config; print(config.OUTPUT_DIR)'` should print the dev path.
- `levels build` (no env-var prefix needed; config.py reads `~/.config/kayak/.env` automatically) writes to that path. Check: `ls -la "$OUTPUT_DIR/index.html"` after the build.
- `git status` is clean after the build.
- `php -S localhost:8000 -t "$OUTPUT_DIR"` serves the styled site; styled nav bar renders (Phase 2's fix is exercised via `$_SERVER['DOCUMENT_ROOT'] = "$OUTPUT_DIR"`).
- Live host unchanged — its `~/.config/kayak/.env` already carries the equivalent setting.

**Risk:**
- A new contributor who skips the convention falls back to the legacy collision behavior. Mitigation: `.env.example` is the canonical fresh-clone reference; CLAUDE.md is the second pointer. The legacy behavior is functional (build still works), it's just dirty.
- `public_html_dev` is not auto-cleaned by uninstall / fresh-clone flows — if a dev decides to start over, manual `rm -rf $OUTPUT_DIR` needed.

## Alternatives considered (replaced by Phase 3 above)

The original draft of this plan presented three resolutions for Issues 4/5 (A: drop the symlinks; B: extend the symlink set; C: status quo + `git checkout` after every build). All three are strictly less desirable than the `OUTPUT_DIR` convention now in Phase 3:

- **Option A — `git rm public_html/*.php` and gitignore.** Worked, but required repo changes and broke a real dev convenience (hot-reload through `php -S` against the repo). Strictly worse than the new convention because it removed an option a developer might want.
- **Option B — extend symlinks to all 6 entry points, teach `_deploy_php_files` to skip symlink targets.** Required code changes in the build pipeline (`src/kayak/web/build/deploy.py:96-107`) plus per-entry-point manual setup; the new-entry-point gap that produced this issue would re-open the moment someone forgets a symlink. The `style.css` case (a build-time copy of `src/kayak/web/static/style.css`, not a source file) didn't fit cleanly into the "symlink to source" model.
- **Option C — status quo + `git checkout` after each build.** Tolerated noise; doesn't address the 6 untracked entry points; every contributor learns the recovery trick the hard way.

The new convention preserves the symlink hot-reload workflow (it still works against the repo's `public_html/`, with Phase 2's PHP fix in place), AND gives a build-target that doesn't clobber the repo. Both modes coexist.

## Risks (overall)

Phase 1 / Phase 2 risks are documented in their respective sections. Cross-cutting risks:

- **Phase 3 relies on documentation discoverability.** A new contributor who never reads `.env.example` or `CLAUDE.md` reproduces the legacy problem. Not a hard-failure mode (build still works) but loses the cleanliness gain. Mitigation: `.env.example` is the standard fresh-clone reference; CLAUDE.md ranks high in attention for AI-assisted contributors.
- **Phase ordering doesn't matter functionally but matters for verification.** Phase 1's lint-config fix is independent. Phase 2's PHP fix and Phase 3's `OUTPUT_DIR` convention both address the styled-nav gap from different angles, so verifying either requires the other's surface to be present (Phase 2 verification needs a dev that runs with default `OUTPUT_DIR` against the repo; Phase 3 verification needs the convention adopted). Land them in plan order; the verification gates compose.

## Out of scope

- **Standalone `biome` / `shellcheck` install** — declined 2026-05-12; pre-commit + CI cover lint enforcement. Revisit if/when local `make lint-*` invocations become needed.
- **`pre-commit autoupdate`** — separate hygiene task; current versions (biome 2.4.11, shellcheck 0.10.0.1) match CI which is what matters.
- **Restructuring `_deploy_php_files` to be incremental** — out of scope; the current full-copy approach is fine for ~30 files and lets Phase 3's "build is mandatory if `OUTPUT_DIR` is set" workflow run in ~6s.
- **`public_html` POSIX ACL audit** — the existing default ACLs (`/home/pat/public_html` rwx for `www-data` per CLAUDE.md) apply to the directory, not individual files. New dev paths (`public_html_dev`) don't need ACLs **if served via `php -S`** (runs as the user). If a dev wants to test under nginx + FPM (closer to prod), the analogous ACL setup from CLAUDE.md applies to the new path — out of scope here; document it locally if/when the case comes up.
- **Editor / contact / proposals security surface** — `docs/done/PLAN_editor_security_review.md` and `docs/done/PLAN_php_layer_split.md` cover that area; the four new `docs/security/*.md` artifacts at `21c9e1a` orthogonal to this plan.
- **Enforcing `OUTPUT_DIR` is set on dev** — e.g. via a Makefile guard. Convention-only is sufficient; tooling enforcement is overkill for a one/two-person dev set.

## Reproduce / verify

Read-only commands.

```bash
# Phase 1 inputs
ls -la php/style.css                    # leftover stale CSS (expected on this host)
grep -n 'php/style.css' biome.json      # → line 15
grep -n 'php/style.css\|hardening' Makefile
ls deploy/*.sh                          # → deploy/install-config.sh
grep -nE 'style.css|reaches-' .gitignore | head    # → php/style.css at line 41 (to remove); reaches-* at 39-40 (keep)

# Phase 2 inputs
sed -n '27,55p' php/includes/header.php          # css_head_block()
grep -n 'DOCUMENT_ROOT' php/includes/gauge_map.php  # the existing precedent
grep -c 'DOCUMENT_ROOT' php/*.php php/includes/*.php | grep -v ':0'  # → 8 files, 12 lines

# Phase 3 inputs
git ls-files -s public_html/*.php | awk '{print $1}' | sort -u     # → 120000 (×24)
for f in csp-report.php custom_gauges.php gauge_picker.php sw.js sitemap.xml style.css; do
  git ls-files public_html/$f >/dev/null && echo "  $f: tracked" || echo "  $f: untracked"
done
sed -n '11,16p' .env.example                     # current OUTPUT_DIR comment (Phase 3 rewrites)
grep -nA2 'OUTPUT_DIR=' deploy/SETUP.md          # the prod template (Phase 3 doesn't change)

# Environment axis check
grep -n 'OUTPUT_DIR' src/kayak/config.py ~/.config/kayak/.env 2>/dev/null
hostname                              # = "levels" on the production host
```

## End state

After all 3 phases:

- `biome.json` no longer references `php/style.css`. `biome check` runs over 11 files (10 JS + 1 CSS) cleanly, no `internalError/io`.
- `Makefile lint-css` reads `biome check src/kayak/web/static/style.css`; `lint-shell` reads `shellcheck --severity=warning scripts/*.sh systemd/*.sh deploy/*.sh`; both run to completion where the standalone tools are installed.
- `.gitignore` covers stray `static/` build artifacts (5 new patterns); the stale `php/style.css` entry is removed (no build code references that path).
- `php/style.css` no longer exists on disk (one-time local `rm`); no gitignore entry shielding it, so any reappearance surfaces in `git status` immediately.
- `php/includes/header.php`'s `css_head_block()` uses `$_SERVER['DOCUMENT_ROOT']` with a `__DIR__/..` fallback; matches the existing pattern in `gauge_map.php`.
- `.env.example` documents the dev `OUTPUT_DIR` convention explicitly (no more "default: public_html/ in repo" without the dev callout).
- `CLAUDE.md` Local Development Setup section calls out the dev `OUTPUT_DIR` convention.
- Live host (`OUTPUT_DIR=/home/pat/public_html`): no behavior change. Production already operates correctly.
- Dev host with the new convention (`OUTPUT_DIR=$HOME/public_html_dev`): `git status` permanently clean after `levels build`. Site renders styled nav. The 24 tracked symlinks under `public_html/` and the 6 untracked entry-point gap are both irrelevant (build doesn't touch the repo).
- Dev host without adopting the convention (default `OUTPUT_DIR`): legacy behavior — typechange noise after build, stragglers in `static/`. Phase 2's PHP fix at least keeps the styled nav working; everything else is "your call to adopt the convention."
- Pre-commit + CI continue to gate lint cleanliness; the live host can run `pre-commit run --all-files` to lint manually.
