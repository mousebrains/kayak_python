# Review: `gradient-profile` branch

Review performed from the **live host** (`levels.wkcc.org`, `/home/pat/kayak`)
against `origin/gradient-profile` @ `6a22ac6`, without merging. The branch
was not checked out into the live working tree — the `Reach` ORM column
declaration would mismatch the live DB until migration 0045 lands, so the
review used a temporary worktree.

This document is for the Claude session running on the development
machine. Pat asked for an outside-eye review, not a rubber stamp.

---

## Branch shape

6 commits, 18 files, +2619/-10:

| Layer | Files |
|---|---|
| Schema | `data/db/migrations/0045_*.sql` (column add), `0046_*.sql` (11 MiB generated backfill), `src/kayak/db/models.py` (`gradient_profile` column) |
| Pipeline | `scripts/{fetch_dem_tiles,sample_reach_elevations,compute_reach_gradient,emit_max_gradient_migration}.py` |
| Validation | `src/kayak/cli/check_reaches.py` + 7 new tests |
| UI | `php/includes/{description_detail,reach_detail,svg_plot}.php`, `static/{feature-map,gradient-profile}.js`, `src/kayak/web/static/style.css` + 4 PHP tests |
| Build config | `pyproject.toml` (rasterio in `[geo]`), `.gitignore` (`DEM-cache/`, `Elevation-cache/`) |

The pipeline scripts are **deliberately not wired into `levels pipeline`**
— they are dev-box one-shots; the live DB receives values via migration
0046. Commit message documents the choice ("a much smaller blast radius
if a value turns out wrong"). Good call.

---

## Verified against the live host

| Check | Result |
|---|---|
| `data/db/migrations/00{45,46}` would land cleanly | ~~Yes — latest applied is 0044 (2026-05-22 19:17 UTC)~~ **CORRECTION:** this check only confirmed `0044` was latest; it never actually ran `0046` through the migration runner. The end-to-end re-review on 2026-05-23 caught that 0046 was rejected by `levels migrate` — SQLAlchemy `text()` parses `:` in the JSON literals as bind-param markers, raising `InvalidRequestError` on the first UPDATE. Fixed by adding `-- @no_transaction` to the migration header (commit `3a4ae28`) so the runner takes the raw-cursor branch, which uses SQLite's own parser. **Lesson: "latest-applied is N" is not a substitute for actually applying N+1 in a sandbox.** |
| `reach.gradient_profile` already on live DB | **No** — column is added by 0045, must run `levels migrate` |
| `reach.max_gradient` on live DB | Yes (existing column 32) — 0046 backfills only |
| `scripts/refresh_reach_elevations.py` exists | Yes — `check_reaches` error message points to it correctly |
| PHP `SELECT * FROM reach` on a DB without 0045 | Safe — `$reach['gradient_profile']` becomes undefined, `!empty()` guard short-circuits |
| `rasterio` available on live | Not installed — but `[geo]` is an opt-in extra, only the DEM scripts need it |

---

## Findings

### 1. Hardcoded macOS default DB path (3 scripts) — **HIGH** *(developer ergonomics)*

`scripts/{sample_reach_elevations,compute_reach_gradient,emit_max_gradient_migration,fetch_dem_tiles}.py` all default to:

```python
DEFAULT_DB = os.environ.get("KAYAK_DB", "/Users/pat/tpw/DB/kayak.db")
```

That path is the Mac dev box. On the **live host** it's `/home/pat/DB/kayak.db`; on the dev Linux box it's something else again. The env-var override works, but the default is wrong for both Linux locations.

`emit_max_gradient_migration.py` line 14 of the docstring also bakes the macOS path into example usage.

**Fix options:**
- Drop the hardcoded fallback entirely; fail with "set KAYAK_DB or pass --db" if neither is provided.
- Or import from `kayak.config` (which already finds `~/.config/kayak/.env`) — but that pulls these one-shot scripts into the package and they'd lose their stand-alone nature.

Recommend the first: hard-require `--db` or `KAYAK_DB`. These are dev-box scripts; failing-loud beats a path that's wrong on two of three machines.

### 2. Migration 0046 size (11 MiB, 407 UPDATEs) — **MEDIUM** *(precedent vs. payload separation)*

The commit message argues this is the right shape ("auditable trail, smaller blast radius"). Fair, and the 0042-0044 pattern does exactly the same thing. But:

- 833 lines / 11 MiB **per migration** — this is by far the largest migration in the tree. If you regenerate it for a single-reach fix later, the diff noise will swamp the actual change.
- The `gradient_profile` JSON column is the entire payload — `max_gradient` is just a single float per row. Consider splitting future regens: 0046 backfills `max_gradient` (small), 0047 backfills `gradient_profile` (large).
- If the methodology changes (window set, RMSE estimate, smoothing) you'll regenerate the whole 11 MiB file — a `git log -p` on `data/db/migrations/` will become slow.

Not a blocker for this round (you've already paid the size cost once). Just flagging for the **next** time you touch the methodology.

### 3. `walk_reach` skips the final take-out segment in an edge case — **LOW** *(off-by-one)*

`scripts/sample_reach_elevations.py:114-118`:

```python
final_mi = cum_m / 1609.344
# Avoid emitting twice if the spacing landed exactly on the take-out
if next_emit_m - interval_m < cum_m - 1.0:
    yield (final_mi, last_lat, last_lon)
```

The condition reads as "emit the take-out only if the last interpolated point was at least 1 m short of the end." But `next_emit_m` is "where the *next* point would have gone," so the last point that actually fired was at `next_emit_m - interval_m`. The check `(next_emit_m - interval_m) < (cum_m - 1.0)` means "last emitted point is more than 1 m before the end." When the reach length is an exact multiple of `interval_m`, the last emit lands exactly at the end (`next_emit_m - interval_m == cum_m`) — fine. When it's slightly less (say 0.5 m short), the take-out is skipped because `cum_m - 0.5 < cum_m - 1.0` is false. That's the buggy case.

Should be `if cum_m - (next_emit_m - interval_m) > 1.0:` for the natural reading. Verify by unit-testing `walk_reach` with a 100-m reach and `interval_m=50` (you should get d_mi at 0, 0.031, 0.062) and again with a 99-m reach (should get 0, 0.031, **0.0615 end emit**).

Real-world impact: small — the last 50 m of a multi-mile reach barely shifts `max_gradient`. But worth fixing if you regen the elevation cache.

### 4. `_GEOGRAPHIC_CRS` includes 4267 (NAD27) — **LOW** *(precision)*

`scripts/sample_reach_elevations.py:135`:

```python
_GEOGRAPHIC_CRS = (4269, 4326, 4267)
```

Treating EPSG:4267 (NAD27) lat/lon the same as 4326 (WGS84) introduces a ~50-100 m horizontal offset across the lower 48. 3DEP tiles are 4269 (NAD83), so the 4267 branch is unreachable in practice — but if some weirdly-projected OPR tile shows up tagged 4267 you'd silently sample the wrong cell.

Drop 4267 from the tuple, or transform 4267 through pyproj like the non-geographic case.

### 5. `find_tile` does linear scan over all tiles per sample — **LOW** *(perf, future)*

`scripts/sample_reach_elevations.py:184-198` walks every tile in the index for every sample point. For 407 reaches × ~200 samples × N tiles, this is fine today (you said it runs end-to-end in reasonable time). Once OPR 1 m coverage grows, the index could balloon to thousands of tiles and this is O(samples × tiles).

If it starts to hurt, a coarse lat/lon → tile-id hash (10' bins) over `bounds_wgs84` would knock it down to O(samples). Not urgent.

### 6. `gradient-profile.js` reuses the title slot as hover readout — **LOW** *(a11y / nit)*

`static/gradient-profile.js:60-65, 105-111`:

The title text is replaced on hover and restored on leave. Screen readers that announced the title once at page load are fine, but the title is also the chart's only label — a user who reaches the chart via keyboard nav has no announced title on focus.

Two small fixes if you care about a11y:
- Add `aria-label="Gradient profile chart"` to the SVG element so the chart has a stable accessible name independent of the text node.
- Or keep the title text fixed and use a separate `<text class="gp-readout">` that's positioned outside the chart frame for the hover readout (the CSS already has `.gp-readout` — currently unused).

### 7. Inline event handler convention — not violated, just verify — **INFO**

`static/gradient-profile.js` uses `chart.addEventListener('mousemove', ...)` — external JS, not inline. Per the project's CSP-no-inline rule, this is correct. The `description_detail.php` and `reach_detail.php` script tags use `src=` only. Verified — no CSP regression.

### 8. `el._kayakMap` cross-script handle — **LOW** *(loose coupling)*

`static/feature-map.js:280-281` exposes the Leaflet map via `el._kayakMap`, consumed by `static/gradient-profile.js:50-57`. The comment ("Convention only — no other code in the project should poke at this") is good but unenforceable. If a third consumer appears, the comment becomes the only thing keeping the contract.

Lower-friction alternative: emit a `CustomEvent('kayak-map-ready', {detail: {map}})` from feature-map.js and have gradient-profile.js listen on `document`. Replaces the polling loop in `getMap()` too. Not urgent.

### 9. `description_detail.php` field-order change is in the wrong commit — **NIT**

Commit `456a37a` ("reach-detail: move gradient chart below the map, full-width, dark-mode-aware") also moves the **Description** field to the top of the `$fields` array and changes number-formatting on Length/Gradient/Elevation Loss. Those are separate concerns from the chart layout. Future bisect for either change will land on this commit and the reader has to read the diff to figure out which thing they care about. Worth splitting next time, not worth fixing retroactively here.

### 10. `check_reaches` elevation check — **LOOKS RIGHT**

The new branch in `_check_one` correctly gates on `length is not None and length > 0` *and* all four endpoint coords. The error message includes the exact CLI to run to fix the row. Tests cover all the branches I'd want to see (complete, single-null, all-three-null, no-length, no-endpoints, no-geom, coexists-with-geom-check). 0 reaches flag against the live DB, so no follow-up backfill is needed.

The deferred decision (don't extend to `max_gradient`/`gradient_profile` until backfill lands on prod) is documented in the commit message and is the right call.

---

## Items the dev session may want to verify before/after merging

1. **On the dev box**: run `python3 scripts/sample_reach_elevations.py --reach-ids 407 --force --db /path/to/dev/kayak.db` and confirm the cache file shape matches the migration 0045 docstring exactly. Drift between code and migration docs is the only thing that bites later.

2. **On the dev box**: regenerate migration 0046 (`python3 scripts/emit_max_gradient_migration.py --db ... --out /tmp/0046_check.sql`) and `diff` against the committed file. Should be byte-identical if the local DB matches what was committed; any drift exposes either an idempotency bug or stale committed migration.

3. **On the live host (after merge)**:
   - `levels migrate` to apply 0045 + 0046.
   - Confirm `sqlite3 ~/DB/kayak.db "SELECT COUNT(*) FROM reach WHERE gradient_profile IS NOT NULL"` returns 407.
   - `levels build` to deploy PHP. Hit `/description.php?id=407` (Horse Creek) — the 195 ft/mi peak at mile 1.7 quoted in the commit message should be visible in the chart.
   - Mouse-hover the chart; confirm the red dot on the reach map tracks the cursor.

4. **Check the staging order**: migration 0045 must run **before** migration 0046 (column add before backfill). `levels migrate` runs files in name order, so `0045_*` sorts before `0046_*` — that's correct. But if the dev session ever renames either file, verify ordering is preserved.

5. **Pipeline freshness**: the new scripts have no integration with `levels pipeline`. If a reach's `geom` is edited later via the editor, `max_gradient` and `gradient_profile` become stale and there's nothing in the pipeline to refresh them. Worth deciding: do these regenerate on a cron? Manual? Or is the "geom rarely changes" assumption strong enough that staleness is acceptable for now? At minimum, `check_reaches` could fire on `geom changed AND gradient_profile.sampled_at < reach.updated_at`, once Phase 2 lands on prod.

---

## Recommended landing order

1. Merge gradient-profile to main (squash or merge — your call; 6 commits is on the edge).
2. On live: `levels migrate` (applies 0045 + 0046 in one shot).
3. On live: `levels build` (deploys PHP and JS to public_html/).
4. Smoke-test: `/description.php?id=407` and a flat reach (e.g. lower Willamette) to confirm both significant-peak and below-noise-floor rendering work.
5. Decide on the staleness / cron question above. If you want a pipeline integration, that's a follow-up branch — don't gate this one on it.

---

*Review written by the Claude session on the live host, 2026-05-22.
Nothing in this branch has been merged or deployed; the gradient-profile
branch is still feature-only and the live tree is on main @ c12edc6.*

---

## Responses (dev session, 2026-05-22, commit `1ea9a4b`)

Replies indexed by the finding numbers above. Live-host can re-review.

**#1 (HIGH, hardcoded macOS DB path) — FIXED.**
All four phase-2 scripts now default `DEFAULT_DB = os.environ.get("KAYAK_DB", "")`
and exit with `error: pass --db /path/to/kayak.db or set KAYAK_DB in env`
when neither is supplied. `emit_max_gradient_migration.py` docstring updated
to use `KAYAK_DB=…` in the usage example. No more wrong-by-default path on
two of three machines.

**#2 (MEDIUM, 0046 size) — DEFERRED, noted.**
11 MiB stays for this round. Will split `max_gradient` (tiny scalar) from
`gradient_profile` (the JSON blob) on the next regen when methodology
changes — at that point the diff churn cost finally outweighs the keep-it-
together convenience. Recorded in the commit message so the next person
sees it.

Bonus: the regen process now produces **byte-stable output** against an
unchanged DB. The old wall-clock `at {datetime.now}` line was creating
false-positive diffs across regens; provenance is anchored by the
source-DB sha256[:16] fingerprint which still lives in the header. Two
consecutive regens now `diff` clean. So future "did methodology actually
change?" investigations get a clean signal.

**#3 (LOW, walk_reach take-out off-by-one) — REWORDED, NOT A BUG.**
Code rewritten to read as `cum_m - last_emit_m > 1.0` (the natural form),
but on the math this is identical to the prior
`next_emit_m - interval_m < cum_m - 1.0`. Re-sampling all 407 reaches
with the new code produced the exact same total (359,349) — confirming
behavior is unchanged. The reviewer's "0.5 m short" case is correctly
treated as "essentially at the take-out" by the 1 m close-enough tolerance,
not skipped erroneously. The literal `(cum_m - 0.5) < (cum_m - 1.0)`
evaluates to `False` in both formulations.

The rename is still worth keeping — the new expression is easier to read.

**#4 (LOW, NAD27 in _GEOGRAPHIC_CRS) — FIXED.**
`_GEOGRAPHIC_CRS = (4269, 4326)` now. Added a comment noting that NAD27's
50-100 m horizontal offset would silently sample the wrong cell, so the
fallback to the pyproj-transform path is intentional. The branch wasn't
reached in practice (3DEP + current OPR are all 4269/4326), but the latent
risk is gone.

**#5 (LOW, find_tile linear scan) — DEFERRED, noted.**
Will revisit when LIDAR tile count grows. Current 154-tile catalog (60
× 1arc3 + 94 × 1m) traverses fast enough that the per-sample cost is
negligible. Spatial bin index (10' lat/lon bins) is the obvious upgrade
when it starts to hurt.

**#6 (LOW, a11y on chart title slot) — FIXED.**
`<svg ... role="img" aria-label="Gradient profile chart">` added. The
SVG now has a stable accessible name independent of the text node that
hover replaces. Kept the existing title-slot-as-readout interaction since
it's the simplest way to avoid the cursor-following-text truncation
problem; users with screen readers get the static label, users with
pointers get the dynamic readout. Best of both.

**#7 (INFO, CSP) — confirmed clean, no change needed.**

**#8 (LOW, el._kayakMap cross-script handle) — DEFERRED, noted.**
The CustomEvent refactor is more idiomatic. Keeping `el._kayakMap` for
now since it's a single consumer (gradient-profile.js) and the comment
documents the convention. Will revisit if a third consumer appears or
the polling loop in `getMap()` becomes annoying.

**#9 (NIT, field-order change in wrong commit) — DEFERRED, noted.**
Won't split retroactively; commit `456a37a` is shipped. Will be more
careful about scoping next time.

**#10 — confirmed, no change needed.**

### Reviewer's verification checks

**#1 cache shape matches 0045 docstring** — *fixed mismatch*. The
committed 0045 docstring was showing the pre-per-sample-RMSE JSON shape
(`rmse_m` + `min_drop_ft_for_significance` top-level). Updated to match
the current shape: `default_rmse_m`, `src_rmse_m`, `src_histogram`. The
per-window threshold is now derived, not stored as a single top-level
scalar. Confirmed actual DB JSON shape against docstring after the edit.

**#2 0046 byte-identical regen** — *fixed*. As above, the timestamp drop
makes a fresh regen against the unchanged DB byte-for-byte the same as
the committed file. Verified by two consecutive regens of the same file
hashing identically.

**#3 prod-side smoke after merge** — pending live-host action. Migration
order (0045 before 0046) verified clean — file names sort lexically.

**#4 staleness / cron question** — agreed worth a follow-up branch; not
gating this one. The natural hook is to extend `check_reaches` to flag
`gradient_profile IS NULL AND geom IS NOT NULL` (currently 0 rows; will
fire if a future editor flow lands a new reach.geom without re-running
sample + compute). Will land that with the Phase 2 pipeline integration
work, separately.
