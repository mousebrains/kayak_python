# Plan — Dual-axis flow + gage-height plot on description.php

> **Cross-check:** Plan drafted 2026-04-20 from macOS dev checkout (`/Users/pat/tpw/kayak/`) against a pulled snapshot of the production DB at `/Users/pat/tpw/DB/kayak.db`. A second Claude session on the live Debian system (DB at `/home/pat/DB/kayak.db`) should re-run the read-only commands in **§Reproduce** below and confirm the findings before any edits land.
>
> Dates are absolute. References are `file:line` against `main` at the time of writing.
>
> **Last verified against `main`:** commit `cc6727d` (2026-04-20). Post-pull check (T3-20 db split, T4-31 PHP test bootstrap) confirmed: `php/description.php` (539 lines), `php/includes/svg_plot.php` (319 lines), `src/kayak/utils/conversions.py:48` (`interpolate_rating`), and the dead `generate_dual_svg_plot` at `php/includes/svg_plot.php:159-307` are all unchanged.

## Goal

When a reach's gauge has **both** a flow-like series (`flow` *or* `inflow`) **and** `gauge` observations in the visible date window, render **one** SVG plot with:

- **Left Y-axis** — Flow (CFS), linear ticks
- **Right Y-axis** — Gage height (ft), ticks at the y-pixel that maps to each "nice" gage-height value via the rating curve (so spacing is non-uniform)
- **One curve only** — the flow line. The right axis is a non-linear *re-labelling* of the same y-coordinate.

When only one of the two series is present, the existing single-axis plot is kept. Temperature is unchanged. Inflow is treated equivalent to flow (the flow-like primary series is `flow` if present, else `inflow`).

## User-confirmed decisions (2026-04-20)

1. **One line.** No second polyline for the gauge series. The right axis is a re-labelling, not an independent series.
2. **Inflow ≡ flow** for the dual-plot trigger and for the y-axis label/units (still CFS).
3. **No rating tables.** The rating curve is derived **only from paired observations supplied by the data provider**. The `rating` and `rating_data` SQL tables are **not consulted**, and `gauge.rating_id` is ignored. Reason: avoid having to keep a separate rating table in sync with the provider's rating.

## Code change summary

### A. New helper in `php/includes/svg_plot.php`

```php
/**
 * Build a piecewise-linear (gauge_ft, flow_cfs) lookup from paired observations.
 *
 * Pairs $primary_type ('flow' or 'inflow') with 'gauge' on matching
 * (source_id, observed_at). Drops $primary value <= 0 (tide/release zeros).
 * Bins the result by gauge_ft (default 50 bins) and emits the median
 * (gauge_ft, flow_cfs) per non-empty bin, sorted by gauge_ft.
 *
 * Returns null if fewer than 2 distinct bins are produced.
 */
function derive_rating_lookup(
    PDO $db,
    int $gauge_id,
    string $primary_type,   // 'flow' or 'inflow'
    string $since           // include some lookback beyond the visible window
): ?array;

/** Forward: gauge ft -> flow cfs (linear interp, clamped at endpoints). */
function rate_gauge_to_flow(array $lookup, float $gauge_ft): ?float;

/** Inverse: flow cfs -> gauge ft (linear interp on the inverted table). */
function rate_flow_to_gauge(array $lookup, float $flow_cfs): ?float;
```

The interp logic mirrors `interpolate_rating()` in `src/kayak/utils/conversions.py:48`.

### B. New `generate_rating_dual_plot()` in `php/includes/svg_plot.php`

```php
function generate_rating_dual_plot(
    array $flow_times,
    array $flow_values,
    array $rating_lookup,     // [[gauge_ft, flow_cfs], ...] sorted
    string $title,
    string $primary_label,    // 'Flow (CFS)' or 'Inflow (CFS)'
    int $width = 800,
    int $height = 350,
    int $target_points = 200
): string;
```

Algorithm:

1. Sort + LTTB-downsample the flow series (`lttb_downsample()`).
2. Compute `[fy_min, fy_max, fy_step]` via `nice_axis()` on flow values.
3. Draw flow polyline + left-axis grid/ticks/labels (reuse logic from `generate_svg_plot()`).
4. **Right-axis tick placement (round gauge values, mapped to flow):**
   - Compute the visible gauge range `[gy_visible_min, gy_visible_max] = [rate_flow_to_gauge(fy_min), rate_flow_to_gauge(fy_max)]`.
   - Pick "nice" gauge-height tick values via `nice_axis()` over that range.
   - For each tick gauge value `g_i`, compute `q_i = rate_gauge_to_flow(g_i)`.
   - Skip ticks where `q_i` falls outside `[fy_min, fy_max]` (no extrapolation onto the plot).
   - Place tick label at the y-pixel of `q_i`.
5. Right-axis label rotated -90° in the right margin.
6. Tick label color contrast: `#C04020` (rust) for right axis to pair visually with the right-axis title, `#666` for left axis (consistent with current).

### C. `php/description.php` rewrite of lines 190-237

Replace the `foreach ($plot_types …)` block with:

```php
function _has_obs(PDO $db, int $gid, string $type, string $since, ?string $until): bool {
    if ($until) {
        $stmt = $db->prepare("SELECT 1 FROM observation o
            JOIN gauge_source gs ON o.source_id=gs.source_id
            WHERE gs.gauge_id=? AND o.data_type=? AND o.observed_at>=? AND o.observed_at<=? LIMIT 1");
        $stmt->execute([$gid, $type, $since, $until]);
    } else {
        $stmt = $db->prepare("SELECT 1 FROM observation o
            JOIN gauge_source gs ON o.source_id=gs.source_id
            WHERE gs.gauge_id=? AND o.data_type=? AND o.observed_at>=? LIMIT 1");
        $stmt->execute([$gid, $type, $since]);
    }
    return (bool)$stmt->fetchColumn();
}

$has_flow   = _has_obs($db, $gauge['id'], 'flow',        $since, $until);
$has_inflow = _has_obs($db, $gauge['id'], 'inflow',      $since, $until);
$has_gauge  = _has_obs($db, $gauge['id'], 'gauge',       $since, $until);
$has_temp   = _has_obs($db, $gauge['id'], 'temperature', $since, $until);

// Primary "flow-like" series: flow takes precedence over inflow.
$primary_type  = $has_flow ? 'flow' : ($has_inflow ? 'inflow' : null);
$primary_label = $primary_type === 'flow'   ? 'Flow (CFS)'
              : ($primary_type === 'inflow' ? 'Inflow (CFS)' : '');

if ($primary_type && $has_gauge) {
    // Use a wider window for the rating lookup so axis ticks cover the visible y-range.
    $lookup_since = date('Y-m-d H:i:s', $latest_ts - 60 * 86400);
    $lookup = derive_rating_lookup($db, $gauge['id'], $primary_type, $lookup_since);

    [$ft, $fv] = _fetch_series($db, $gauge['id'], $primary_type, $since, $until);
    if ($lookup !== null && count($ft) >= 2) {
        echo '<div class="plot-container">'
           . generate_rating_dual_plot($ft, $fv, $lookup,
                                       htmlspecialchars($name) . " — $primary_label / Gage Height",
                                       $primary_label)
           . '</div>';
    } else {
        // Fallback: render two single-axis plots (current behavior).
        _render_single_plot($ft, $fv, $name, $primary_label, true);
        [$gt, $gv] = _fetch_series($db, $gauge['id'], 'gauge', $since, $until);
        _render_single_plot($gt, $gv, $name, 'Gage Height (Ft)', false);
    }
} elseif ($primary_type) {
    [$ft, $fv] = _fetch_series($db, $gauge['id'], $primary_type, $since, $until);
    _render_single_plot($ft, $fv, $name, $primary_label, true);
} elseif ($has_gauge) {
    [$gt, $gv] = _fetch_series($db, $gauge['id'], 'gauge', $since, $until);
    _render_single_plot($gt, $gv, $name, 'Gage Height (Ft)', false);
}

if ($has_temp) {
    [$tt, $tv] = _fetch_series($db, $gauge['id'], 'temperature', $since, $until);
    _render_single_plot($tt, $tv, $name, 'Temperature (F)', false);
}
```

### D. Delete the dead `generate_dual_svg_plot()` (`php/includes/svg_plot.php:159-307`)

It implements two **independent linear** axes, isn't called anywhere, and is superseded by `generate_rating_dual_plot()`.

### E. Out of scope

- `plot.php` and `api.php` keep their per-type endpoints. (`plot.php` already lists `'dual'` in `$valid_types` but no caller exists; we leave it for a follow-up.)
- `levels build` (static index pages with sparklines): unchanged. Sparklines are tiny and a dual axis would clutter them.
- Python pipeline: unchanged. The empirical rating curve is a presentation-time concern.

## Edge cases & decisions

| Case | Behavior |
|---|---|
| Both flow+gauge, paired observations available | Dual-axis plot with empirical rating from the lookup window. |
| Both inflow+gauge (no flow) | Same — inflow is the primary series, axis labelled "Inflow (CFS)". |
| Both flow AND inflow + gauge | Flow wins; inflow is dropped (matches existing rule). |
| Only flow / only inflow / only gauge | Existing single-axis plot. |
| Both series present but no paired timestamps in lookup window | Fall back to two single-axis plots (no regression). |
| `flow > 0` filter excludes tide-driven zeros (3378 such rows in the snapshot) | Lookup curve only; flow line itself is plotted as-is. |
| Gauge value outside lookup range on the plot | Right-axis tick simply not drawn (no extrapolation). Flow line unaffected. |
| Calc-only gauges (e.g. `S_Santiam_Waterloo_merge`) | Same as native — `observation` rows look identical. |
| Flow range spans a near-flat region of the rating curve | Right-axis ticks bunch up — accurate visual cue that flow is insensitive to gauge there. |
| Mobile / narrow viewport | SVG `viewBox` scales; right margin already widens to `mr=80`. |
| Caching | `description.php` already sets `Cache-Control: max-age=300`. The empirical-curve query is one extra `SELECT` per page-load. |

## Test reaches (live DB)

| Reach | Gauge | Why |
|---|---|---|
| **3930** — North Santiam | 1478 (USGS 14178000) | 5712 paired flow+gauge in last 10 days, narrow gauge range (3.34–3.96 ft). Rating ~linear in window — right-axis ticks should be roughly evenly spaced. |
| **4988** — Willamette Albany | 1916 | Gauge 3.21–15.23 ft, flow 5320–41100 CFS over 30 days. Rating clearly **non-linear** — right-axis ticks should visibly bunch toward the bottom. |
| **4995** — Willamette Harrisburg | 1922 (`rating_id=85`, but `rating_data` empty) | Confirms we ignore `rating_id` and rely on paired observations only. |
| Any reach on **gauge 542 — Elk_Trail_merge** | (no `rating_id`, both types) | Confirms dual plot does not gate on `rating_id IS NOT NULL`. |
| Flow-only reach (any USGS-flow-only) | — | Confirms single-plot behavior unchanged. |
| Gauge-only reach (NWS-only) | — | Same. |
| **5781** — Salem (multi-source merge) | 1919 | Confirms merged sources work in the pairing query. |

## Verification steps

1. `SQLITE_PATH=/Users/pat/tpw/DB/kayak.db php -S localhost:8000 -t public_html` (dev) or use the live nginx (prod).
2. Load `/description.php?id=4988` — expect one dual-axis plot, right ticks visibly non-uniform.
3. Load `/description.php?id=3930` — one dual-axis plot, right ticks roughly linear.
4. Load `/description.php?id=4995` — same; confirms empirical fallback.
5. Load a flow-only reach — single flow plot.
6. Load a gauge-only reach — single gauge plot.
7. Visual check: title, both axis labels, polyline, gridlines, x-axis dates, no PHP warnings in `/var/log/nginx/error.log`.
8. `pytest` (no Python changes; smoke).
9. **PHPUnit (added 2026-04-20 in T4-31)** — `vendor/bin/phpunit` should pass with new tests for the rating-curve helpers (see §F below).

## F. PHP unit tests (uses the new bootstrap)

The PHP test scaffolding landed in commit `9412441` (`phpunit.xml`, `tests/php/bootstrap.php`, `composer.json`). Add `tests/php/SvgPlotTest.php`:

```php
<?php
use PHPUnit\Framework\TestCase;
require_once __DIR__ . '/../../php/includes/svg_plot.php';

final class SvgPlotTest extends TestCase {
    public function test_rate_gauge_to_flow_linear_interp(): void {
        $lookup = [[3.0, 100.0], [4.0, 200.0], [5.0, 400.0]];
        $this->assertSame(150.0, rate_gauge_to_flow($lookup, 3.5));
        $this->assertSame(100.0, rate_gauge_to_flow($lookup, 2.0));   // clamped low
        $this->assertSame(400.0, rate_gauge_to_flow($lookup, 9.0));   // clamped high
    }

    public function test_rate_flow_to_gauge_inverse(): void {
        $lookup = [[3.0, 100.0], [4.0, 200.0], [5.0, 400.0]];
        $this->assertSame(3.5, rate_flow_to_gauge($lookup, 150.0));
        $this->assertSame(4.5, rate_flow_to_gauge($lookup, 300.0));   // mid of [200,400]
    }

    public function test_derive_rating_lookup_from_pairs(): void {
        $pdo = new PDO('sqlite::memory:');
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->exec("CREATE TABLE gauge_source (gauge_id INT, source_id INT);
                    CREATE TABLE observation (source_id INT, observed_at TEXT,
                                              data_type TEXT, value REAL);
                    INSERT INTO gauge_source VALUES (1, 1);");
        // 3 paired flow+gauge points
        $pdo->exec("INSERT INTO observation VALUES
            (1, '2026-04-01 00:00', 'flow',  100.0),
            (1, '2026-04-01 00:00', 'gauge',   3.0),
            (1, '2026-04-01 01:00', 'flow',  400.0),
            (1, '2026-04-01 01:00', 'gauge',   5.0);");
        $r = derive_rating_lookup($pdo, 1, 'flow', '2026-01-01');
        $this->assertNotNull($r);
        $this->assertCount(2, $r);   // 2 distinct gauge_ft buckets
    }

    public function test_generate_rating_dual_plot_emits_svg(): void {
        $svg = generate_rating_dual_plot(
            [time(), time()+3600], [100.0, 200.0],
            [[3.0, 100.0], [4.0, 200.0]],
            'Test', 'Flow (CFS)'
        );
        $this->assertStringContainsString('<svg', $svg);
        $this->assertStringContainsString('Flow (CFS)', $svg);
        $this->assertStringContainsString('Gage Height', $svg);
    }
}
```

`bootstrap.php` (`tests/php/bootstrap.php`) does not need changes — `svg_plot.php` is a leaf include with no PDO side effects. The test file's own in-memory PDO setup is enough.

`phpstan.neon` (also new in T4-31) will lint the new helpers — keep types declared (`array`, `?float`, `string`).

## Estimated diff

- New helper functions in `php/includes/svg_plot.php`: ~80 lines (`derive_rating_lookup`, `rate_*`, `generate_rating_dual_plot`).
- Delete `generate_dual_svg_plot` from `php/includes/svg_plot.php:159-307`: ~150 lines.
- `php/description.php` lines 190-237 rewrite: net ~−10 lines (logic is simpler with helpers).
- Net: +~80 lines, −~160 lines.

## Reproduce

```sh
DB=${DB:-/home/pat/DB/kayak.db}   # macOS dev: /Users/pat/tpw/DB/kayak.db

# Confirm rating_data is empty (justifies skipping rating tables).
sqlite3 "$DB" "SELECT COUNT(*) FROM rating_data;"
# expected: 0

# Confirm gauges with both flow+gauge paired observations.
sqlite3 "$DB" "SELECT COUNT(DISTINCT gs.gauge_id) FROM gauge_source gs
  JOIN observation of ON of.source_id=gs.source_id AND of.data_type='flow'
  JOIN observation og ON og.source_id=gs.source_id AND og.data_type='gauge';"
# expected: ~173

# Spot-check rating-curve range for Willamette Albany (gauge 1916).
sqlite3 "$DB" "WITH p AS (
  SELECT f.value AS flow, g.value AS gauge
  FROM observation f
  JOIN observation g ON f.observed_at=g.observed_at AND f.source_id=g.source_id
  JOIN gauge_source gs ON f.source_id=gs.source_id
  WHERE gs.gauge_id=1916 AND f.data_type='flow' AND g.data_type='gauge'
) SELECT MIN(flow), MAX(flow), MIN(gauge), MAX(gauge), COUNT(*) FROM p;"
# expected: ~5320, ~41100, ~3.21, ~15.23, ~25000

# Confirm the dead generate_dual_svg_plot has no callers.
grep -rn 'generate_dual_svg_plot' /home/pat/public_html /home/pat/kayak 2>/dev/null
# expected: only the definition in php/includes/svg_plot.php
```
