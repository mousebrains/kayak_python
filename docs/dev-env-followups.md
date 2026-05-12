# Dev-environment follow-ups

Pre-existing issues uncovered during the JS cleanup work (PLAN_js_cleanup Phase 1–3). All predate that work and were deferred per "we'll fix them later." Listed roughly in increasing scope.

> **Symptoms depend on `OUTPUT_DIR`.** This document was written from a dev machine where `OUTPUT_DIR` is unset and defaults to `BASE_DIR/public_html` (`src/kayak/config.py:35`) — i.e., `levels build` writes back into the repo's `public_html/`, on top of the dev-convenience symlinks. On the live host (`levels`), `~/.config/kayak/.env` sets `OUTPUT_DIR=/home/pat/public_html` (a sibling outside the repo), so the build never touches the repo's tree.
>
> Consequences: issues 3, 4, and 5 below describe symptoms (empty `<style>` block, stray `static/` duplicates, `git status` typechanges after build) that only appear when the build target collides with the repo. On the live host these are silent — but the underlying inconsistencies (the symlinks, the missing entry-point coverage, the stale lint refs) are still present and still worth fixing. Issues 1 and 2 are environment-independent: they bite anyone who runs `make lint-css` or `make lint-shell`.

## 1. `php/style.css` stale ref in `biome.json` + `Makefile`

**State:** Both files reference `php/style.css`, but the file doesn't exist on disk.

- `biome.json:15` — `"php/style.css"` in `files.includes`.
- `Makefile:35` — `lint-css` target reads `biome check php/style.css src/kayak/web/static/style.css`.

**Why it's stale:** Commit `a4e1e02` ("Fix PHP nav bar: copy style.css to php/ during build", 2026-04-11) changed `php/style.css` from a tracked source file to a build artifact written by `src/kayak/web/build/deploy.py:107` to `public_html/style.css`. The two references missed the cleanup pass.

**Symptom:** Biome emits `internalError/io  × No such file or directory` for `php/style.css` but exits 0. CI's `biome check` is noisy but the gate doesn't fail. `Checked 1 file` instead of 2 in `make lint-css`.

**Fix (one-line, both places):**

- Remove `"php/style.css"` from `biome.json:files.includes`.
- Change `Makefile lint-css` to `biome check src/kayak/web/static/style.css`.

## 2. `hardening/*.sh` stale glob in `Makefile lint-shell`

**State:** `Makefile:38` reads:

```
shellcheck --severity=warning scripts/*.sh systemd/*.sh hardening/*.sh
```

The `hardening/` directory was folded into `deploy/` by commit `07e2f33` ("deploy: fold hardening/ into deploy/").

**Why it's stale:** Commit `8d3280f` ("ci: drop hardening/*.sh from shell lint glob") fixed the same issue in `.github/workflows/ci.yml:` but didn't touch the Makefile.

**Symptom:** Bash passes the unmatched `hardening/*.sh` glob through literally; shellcheck exits non-zero; `make lint-shell` and `make lint-all` halt with `*** [lint-shell] Error 2`. CI is unaffected.

**Fix:** mirror `8d3280f` in the Makefile — drop `hardening/*.sh` from line 38. If `deploy/` now has shell scripts that should be linted, add `deploy/*.sh` (verify with `ls deploy/*.sh`).

## 3. `public_html/includes` symlink defeats `__DIR__/..` resolution in PHP

**State:**

```
public_html/includes -> ../php/includes   (symlink, set up Mar 1 2026)
```

The symlink exists locally (this dev box) for hot-reload — edits to `php/includes/*.php` show up immediately under `public_html/includes/` without rebuild. Production-side rsync deploys with a real directory, so the issue is dev-only.

**Failure mode:** PHP's `__DIR__` resolves through symlinks. When `php/includes/header.php` is loaded *as* `public_html/includes/header.php` (via the symlink), `__DIR__` resolves to the symlink target (`php/includes`), so `__DIR__ . '/..'` gives `php/` instead of `public_html/`. Concretely, in `css_head_block()` (`php/includes/header.php:27-45`):

```php
$doc_root = __DIR__ . '/..';
$hash_path = $doc_root . '/static/style.css.hash';   // resolves to php/static/style.css.hash — doesn't exist
// fallback:
$path = $doc_root . '/style.css';                    // resolves to php/style.css — doesn't exist either
$css = is_readable($path) ? (string)file_get_contents($path) : '';
$block = "<style>\n$css\n</style>";                  // empty <style></style>
```

**Symptom:** Locally, every PHP page (description, gauge, picker, etc.) renders with empty `<style></style>` block — no styled nav bar, no styled popup positioning, etc. Affected Phase 1, 2, and 3 smoke tests.

**Fix options (pick one):**

- **(a) Replace symlink with real directory** (loses hot-reload; need `levels build` after each `php/` edit):
  ```bash
  rm public_html/includes && mkdir public_html/includes && cp -p php/includes/* public_html/includes/
  ```
- **(b) Fix PHP to be symlink-aware** — make `css_head_block()` (and any other `__DIR__/..` consumers) use `realpath(__DIR__ . '/..')` *or* a script-determined doc root (e.g., `$_SERVER['DOCUMENT_ROOT']`), so the path resolves correctly regardless of how the file was loaded. Lowest dev-impact; smallest diff.
- **(c) Set `OUTPUT_DIR` to a non-symlink path during dev** — e.g., `OUTPUT_DIR=~/public_html-dev levels build && php -S localhost:8000 -t ~/public_html-dev`. Tedious.

Recommend (b) — a one-or-two-line PHP fix that keeps the symlink convenience and makes prod/dev behave identically.

## 4. Stray duplicates in `static/`

**State:** The repo's `static/` directory has these untracked files that don't belong:

- `static/levels.js` — identical to `src/kayak/web/static/levels.js` (canonical source).
- `static/filters.js` — identical to `src/kayak/web/static/filters.js`.
- `static/sparklines.json` — build artifact (per-build output).
- `static/style-<hash>.css` — build artifact (hashed CSS).
- `static/style.css.hash` — build artifact (sidecar for the hashed CSS).
- `static/reaches-geom.json`, `static/reaches-state.json` — build artifacts (already gitignored, just noise).

**Origin:** Probably leftover from an earlier build pipeline that wrote to `BASE_DIR/static/` instead of `public_html/static/`. Current `deploy.py` correctly targets `output_dir/static/`, but the leftovers were never cleaned.

**Symptom:** These show as Untracked in every `git status`. The hashed style file name changes each build, so the noise grows over time.

**Fix:** Delete the duplicates and gitignore the build-artifact patterns.

```bash
rm static/levels.js static/filters.js static/sparklines.json \
   static/style-*.css static/style.css.hash
```

And add to `.gitignore`:

```
static/levels.js
static/filters.js
static/sparklines.json
static/style-*.css
static/style.css.hash
```

(Or — better — clean these up *inside* `levels build` itself: have `_deploy_static_assets` orphan-sweep stray output from the source directory.)

## 5. `public_html/*.php` symlinks vs build-time regular files

**State:** Many `public_html/*.php` files are tracked as **symlinks** (git mode `120000`) pointing to `../php/<name>.php`. After `levels build`, they're replaced with regular files (build's `shutil.copy2` doesn't preserve symlink semantics; it writes a real file). Other PHP entry points (`csp-report.php`, `custom_gauges.php`, `gauge_picker.php`, `sw.js`, `sitemap.xml`, `style.css`) are NOT tracked at all — they only exist as build output.

**Symptom:** After `levels build`, `git status` shows 24 typechanges for the tracked symlinks plus 6+ untracked build artifacts. Re-running `levels build` doesn't shrink the noise; only `git checkout public_html/*.php` restores the symlinks.

**Two underlying inconsistencies:**

- The dev-symlink setup covers a subset of PHP entry points. Newer entry points (added after the initial symlink setup) weren't given symlinks, so the build adds them as regular files in untracked positions.
- `levels build` doesn't know it's running against a symlink-tracked dev environment; it always writes regular files.

**Fix options:**

- **(a) Drop the symlinks; gitignore all of `public_html/`**. Most CI-friendly; aligns with "public_html is build output, not source." Requires `levels build` after every `php/` edit during dev, which is the production model. Aligns with [issue 3] option (a).
- **(b) Keep symlinks; extend the symlink set to all PHP entry points** AND teach `levels build` to skip files that are symlinks (treat them as opt-out-of-deploy). Most dev-ergonomic but adds build complexity.
- **(c) Status quo + `git checkout public_html/*.php` after each build.** Tolerated, noisy.

If [issue 3] gets fixed via PHP-side `realpath()` ([option b]), [issue 5] becomes purely a hygiene concern.

## Suggested ordering when these are addressed

1. **Issues 1 + 2** together — one tiny `ci: drop stale lint refs` commit. Five minutes total.
2. **Issue 3 option (b)** — PHP `realpath()` patch in `css_head_block()`. Eliminates the dev-vs-prod styling gap.
3. **Issue 4** — delete stray static/ duplicates + gitignore patterns. Trivial.
4. **Issue 5** — bigger; tied to issue 3's resolution. Defer until it actually matters.

All five are out of scope for any in-flight plan (JS cleanup is complete; production discipline / editor security / PHP layer split don't touch this surface).
