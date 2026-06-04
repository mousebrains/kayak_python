# scripts/regression — gauge-pair regression fitting

Standalone Python tool to fit (multi-)linear regressions between USGS
daily-mean flow series. Used to derive `calc_expression` formulas that
substitute a retired or intermittent gauge with an estimate from a
still-active gauge.

The tool fetches USGS daily means directly via `waterservices.usgs.gov`
(cached to `/tmp/<site>_dv.tsv`), fits OLS with full parameter
covariance, and emits a markdown analysis report plus the
`calc_expression` column values ready to paste into a new
`calc_expression.csv` row in the `kayak_data` repo.

**Dependencies:** Python stdlib + numpy + `curl` on `PATH`. No kayak
imports, so the tool runs without the project venv.

## Common cases

### Single-predictor linear (the usual)

```bash
python3 scripts/regression/gauge_pair_linear.py \
    --predictor 14330000 \
    --target    14328000 \
    --start     1985-01-01 \
    --end       2024-06-09 \
    --name      rogue_14328000_from_14330000 \
    --calc-handle rp::14330000 \
    --out       docs/regression/rogue_14328000_from_14330000.md
```

### Multi-linear (two or more predictors)

Repeat `--predictor`. Each predictor adds one column to the design
matrix:

```bash
python3 scripts/regression/gauge_pair_linear.py \
    --predictor 14330000 --predictor 14337600 \
    --target    14328000 --start 1985-01-01 --end 2024-06-09 \
    --name      rogue_14328000_multi \
    --calc-handle rp::14330000 --calc-handle ms::14337600 \
    --out       docs/regression/rogue_14328000_multi.md
```

### Quadratic terms

`--quadratic` adds an x² column for **every** predictor (so a
single-predictor quadratic is `y = b0 + b1·x + b2·x²`, multi-predictor
quadratic is `y = b0 + Σ bᵢ·xᵢ + Σ cᵢ·xᵢ²`):

```bash
python3 scripts/regression/gauge_pair_linear.py \
    --predictor 14330000 --quadratic \
    --target 14328000 --start 1985-01-01 --end 2024-06-09 \
    --name   rogue_14328000_quadratic --out docs/regression/...
```

`--quadratic-for SITE` (repeatable; mutually exclusive with
`--quadratic`) squares only the named predictor(s). Use it when an
all-predictor quadratic fit shows a squared term whose block-bootstrap
CI straddles zero: a non-significant x² coefficient is an extrapolation
hazard (at the high end of the predictor range it can contribute
hundreds of cfs of phantom signal), so drop it and keep only the
significant squared term(s):

```bash
python3 scripts/regression/gauge_pair_linear.py \
    --predictor 14307620 --predictor 14325000 \
    --quadratic-for 14307620 \
    --target 14323100 --start 1967-10-01 --end 1973-06-30 \
    --name   smith_14323100_from_siuslaw_sfcoquille --out docs/regression/...
```

Reach for quadratic when the residual table in a prior linear fit shows
clear curvature across predictor quintiles (e.g. systematic
under-estimate at low flow and over-estimate at high flow). For most
gauge pairs, a linear fit is sufficient.

## What you get

For every run, the script writes three sibling files to `--out`'s directory:

- `<slug>.md` — the markdown analysis report (described below).
- `<slug>.svg` — a residuals scatter plot (predictor flow on x, residual
  on y) with a ±1.96·σ̂ 95% band. Self-contained (no CSS / JS deps);
  served as `<img src="/static/regression/<slug>.svg">` from PHP gauge
  detail pages by the kayak build. Subsampled to ≤1500 points using a
  slug-seeded RNG so the plot is deterministic across reruns.
- `<slug>.json` — structured fit summary (coefs, full covariance matrix,
  r²/RMSE/σ̂, window). Consumed by PHP `_render_gauge_regression()` to
  render the per-gauge fact-box. Schema is uniform across single /
  multi / quadratic — `coefs[]` is the iteration target (`quadratic` stays
  a bool; `quadratic_sites` lists which predictors carry an x² term).

The markdown report contains:

- **Coefficients with autocorrelation-aware uncertainty.** Each term
  shows the OLS SE *and* a **block-bootstrap SE/95% CI** (resample whole
  monthly blocks), plus a **VIF**. Daily streamflow residuals are
  strongly autocorrelated, so the OLS SEs are optimistic — typically
  ~2–3× too small — and the block-bootstrap figures are the ones to
  quote. VIF > 10 flags a predictor whose individual coefficient is not
  safely interpretable (multicollinearity), which is common when the
  predictors are gauges on the same river.
- **Full parameter variance-covariance matrix** plus the correlation
  matrix. This is the **OLS** (IID) covariance — optimistic for the same
  autocorrelation reason; the caveat block restates the measured lag-1
  residual autocorrelation and SE inflation. High off-diagonal
  correlations (|ρ| > 0.7) are flagged — typically OLS where
  `mean(x) ≠ 0`, which recentering would decouple.
- **Goodness-of-fit:** r², plain RMSE (sqrt(RSS/n)), and the unbiased
  σ̂ (sqrt(RSS/(n−p))).
- **Window stability table** at five default start dates (`--start`
  −5y through +15y, capped at the window end) plus 1990-01-01 and
  earliest-overlap. Lets you eyeball how the fit drifts.
- **Residual diagnostics:** percentile distribution, mean/std/n by
  predictor-1 quintile, and a **by-hydrologic-season** bias table
  (heavy-rain Nov–Dec / light-rain Jan–Feb / rain-on-snow Mar–Apr /
  dry-season May–Oct). Seasonal bias that the pooled diagnostics
  average away — common in this PNW monsoonal basin — shows up here as a
  large mean residual relative to σ̂ in one season.
- **`calc_expression` column values** (expression / time_expression /
  note / provenance_slug) with the right `prefix::gauge` reference
  handles, ready for a new `calc_expression.csv` row in `kayak_data`.
  calc rows are metadata, so they ship via the CSV + `levels
  sync-metadata` — **not** via a migration (a new migration writing a
  metadata table fails `test_migrations_schema_only.py`).
- A **Reproduce** snippet identical to the command that generated the
  file.

Output also goes to stderr as a one-line summary so it's easy to grep:

```
SUMMARY: 14328000 ~ intercept=-292.7±2.762, x1=+0.8285±0.001883, r²=0.9575, RMSE=117.1, n=8599
```

## Companion: sub-daily lead/lag (`gauge_lead_lag.py`)

[`gauge_lead_lag.py`](gauge_lead_lag.py) measures the sub-daily travel-time
structure a daily fit averages away, from USGS **unit values** resampled to a
**30-min** grid (`--grid-minutes`):

```bash
python3 scripts/regression/gauge_lead_lag.py \
    --predictor 14162500 --predictor 14158850 \
    --predictor 14159500 --predictor 14161500 \
    --target 14159000 --start 1987-10-01 --end 1994-09-30 \
    --grid-minutes 30 --name mckenzie_14159000_leadlag \
    --out  docs/regression/mckenzie_14159000_leadlag.md
```

It estimates each predictor's lag by cross-correlating **first differences**
(flow *changes* propagate; baseline levels are near-identical across
neighbours), then compares regression RMSE for two alignments on one shared
hold-out grid:

- **full** — every identifiable predictor at its best lag, including
  *downstream* gauges shifted to a *future* reading (real timing signal, but
  non-causal look-ahead — not realisable in real time);
- **deployable (causal)** — only *upstream* predictors shifted (a *past*
  reading, usable in a nowcast); downstream/unidentifiable held contemporaneous.

The RMSE difference for each is wrapped in a **block-bootstrap CI** (7-day
blocks) — grid residuals are ~0.97 autocorrelated, so the bare difference is far
less precise than its decimals suggest. **Diagnostic only**; writes `<name>.md`
+ a CCF-vs-lag `.svg` + a `<name>.json` summary. Across every kayak calc reach
analysed the *deployable* gain is nil (the active predictors are downstream), so
the verdict is always "keep contemporaneous readings".

**Embedding into the daily report:** run `gauge_lead_lag.py` first (it writes the
JSON), then regenerate the daily report with `gauge_pair_linear.py --leadlag
<slug>` to pull the lags + verdict into a *Sub-daily lead/lag* section. Order
matters — the JSON must exist before the daily regen.

Notes:
- **Resolution** is capped by the *coarser* series — a 30-min target can't be
  resolved finer than 30 min; finer grids add noise without information.
- **Flow vs stage:** prefers discharge (`00060`), falls back to gage height
  (`00065`) per gauge — fine for timing (USGS derives flow from stage
  instantaneously, so they're time-locked); the RMSE comparison is only emitted
  when every series is flow (it applies flow coefficients).
- Pre-2007 unit values are served only by the `nwis.waterservices.usgs.gov`
  host; the script fetches unfiltered (cached to `/tmp/leadlag_<site>_<year>.tsv`).

## Caveats

- **Prediction interval ≠ parameter CI.** The reported SEs are
  parameter uncertainties (precision of the slope/intercept estimates
  given the fitted residuals). A single-day prediction at a new `x`
  has uncertainty dominated by σ̂ (residual scatter), not by SE(slope)
  · x + SE(intercept). The script's note section spells this out.
- **OLS SEs assume IID residuals — they don't hold here.** Daily flow
  residuals are strongly autocorrelated (measured lag-1 ≈ 0.7 for the
  McKenzie fit), so the OLS SEs understate the truth by **~2–3×** (not
  "slightly"); effective n is a fraction of nominal n. The report now
  carries **block-bootstrap** SEs/CIs (monthly blocks) alongside the OLS
  ones — quote those. Point estimates, r², and σ̂ are unaffected (OLS
  stays unbiased; σ̂ is a valid *marginal* single-day scatter). The
  companion `gauge_lead_lag.py` applies the same block bootstrap (7-day
  blocks) to its RMSE-difference, so its "is the gain real?" CI reflects
  the effective sample size — for McKenzie Bridge the lead/lag gain's CI
  straddles zero.
- **No data quality flag check.** USGS daily means in the script's
  cache include the agency's `provisional`/`approved` flag, but it's
  ignored. Approved values dominate in any pre-2024 window so this is
  fine in practice; if a fit ever falls inside the recent
  provisional-only window, document it explicitly.

## Future

- **Piecewise-linear by predictor regime.** When residual quintiles
  show systematic bias (high in Q1, low in Q5 or vice versa) and
  `--quadratic` doesn't capture it well, splitting the predictor range
  into 2–3 regimes and fitting one linear model per regime can halve
  RMSE without adding many free parameters. Compose via
  `greatest(low_estimate, high_estimate)` or
  `if(x < threshold, …, …)`-style expressions, both supported by
  `_safe_eval`.
- **Bayesian variant** with priors on slope/intercept derived from
  watershed geometry (basin area ratio, elevation drop). Out of scope
  for the current use case but a natural extension if we ever need
  many fits with small overlap windows.
