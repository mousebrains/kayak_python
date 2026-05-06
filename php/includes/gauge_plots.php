<?php
declare(strict_types=1);
/**
 * Shared gauge-centric plot/data helpers.
 *
 * Used by description.php (reach detail) and gauge.php (gauge detail).
 * All queries are gauge-keyed — a reach page dereferences to reach.gauge_id
 * before calling in.
 */
require_once __DIR__ . '/svg_plot.php';

/** True iff the gauge's latest observation of $type is within the last $hours. */
function _gp_has_current_obs(PDO $db, int $gauge_id, string $type, int $hours): bool {
    $stmt = $db->prepare(
        'SELECT MAX(o.observed_at) FROM observation o
         JOIN gauge_source gs ON o.source_id = gs.source_id
         WHERE gs.gauge_id = ? AND o.data_type = ?'
    );
    $stmt->execute([$gauge_id, $type]);
    $latest = $stmt->fetchColumn();
    if (!$latest) return false;
    $ts = strtotime((string)$latest . ' UTC');
    if ($ts === false) return false;
    return $ts >= (time() - $hours * 3600);
}

/** True iff at least one observation of $type exists for the gauge in [since, until]. */
function _gp_has_obs(PDO $db, int $gauge_id, string $type, string $since, ?string $until): bool {
    if ($until !== null) {
        $stmt = $db->prepare(
            "SELECT 1 FROM observation o
             JOIN gauge_source gs ON o.source_id = gs.source_id
             WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ? AND o.observed_at <= ?
             LIMIT 1"
        );
        $stmt->execute([$gauge_id, $type, $since, $until]);
    } else {
        $stmt = $db->prepare(
            "SELECT 1 FROM observation o
             JOIN gauge_source gs ON o.source_id = gs.source_id
             WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ?
             LIMIT 1"
        );
        $stmt->execute([$gauge_id, $type, $since]);
    }
    return (bool)$stmt->fetchColumn();
}

/** Lower-bound binary search in a sorted int array. Returns insertion index. */
function _gp_lower_bound(array $sorted, int $target): int {
    $lo = 0; $hi = count($sorted);
    while ($lo < $hi) {
        $mid = ($lo + $hi) >> 1;
        if ($sorted[$mid] < $target) $lo = $mid + 1; else $hi = $mid;
    }
    return $lo;
}

/**
 * Hampel-style spike rejection on one source's time series.
 *
 * For each point, compute the local median and MAD over a ±$half_window_s
 * window of the same source. Drop the point if its deviation from the
 * local median exceeds the threshold, where the threshold is
 *   max( $k * 1.4826 * MAD,  0.25 * |median| + 0.5 )
 *
 * The MAD term (k=3, 1.4826 = robust σ-estimator) catches outliers when
 * there's natural variability in the window. The relative+floor fallback
 * catches obvious spikes against a clean (MAD≈0) signal — without it the
 * Hampel filter degenerates to "keep everything" whenever a sensor is
 * sitting still. The 0.5 unit floor avoids dropping legitimate noise on
 * near-zero stages.
 *
 * Windows with fewer than 3 points don't have enough info; the point
 * stays.
 */
function _gp_despike_per_source(array $times, array $values, array $sources,
                                int $half_window_s = 900, float $k = 3.0): array {
    $n = count($times);
    if ($n === 0) return [$times, $values, $sources];
    // Group input indices by source.
    $by_source = [];
    foreach ($sources as $i => $sid) {
        $by_source[$sid][] = $i;
    }
    $keep = array_fill(0, $n, true);
    foreach ($by_source as $indices) {
        // Sort this source's points by time.
        usort($indices, fn($a, $b) => $times[$a] <=> $times[$b]);
        $st = []; $sv = [];
        foreach ($indices as $i) { $st[] = $times[$i]; $sv[] = $values[$i]; }
        $m = count($st);
        for ($i = 0; $i < $m; $i++) {
            $lo = _gp_lower_bound($st, $st[$i] - $half_window_s);
            $hi = $i;
            while ($hi < $m - 1 && $st[$hi + 1] <= $st[$i] + $half_window_s) $hi++;
            $w = $hi - $lo + 1;
            if ($w < 3) continue;
            $window = array_slice($sv, $lo, $w);
            sort($window);
            $median = ($w & 1)
                ? $window[intdiv($w, 2)]
                : ($window[intdiv($w, 2) - 1] + $window[intdiv($w, 2)]) / 2.0;
            $abs_dev = [];
            foreach ($window as $v) $abs_dev[] = abs($v - $median);
            sort($abs_dev);
            $mad = ($w & 1)
                ? $abs_dev[intdiv($w, 2)]
                : ($abs_dev[intdiv($w, 2) - 1] + $abs_dev[intdiv($w, 2)]) / 2.0;
            $threshold = max($k * 1.4826 * $mad, 0.25 * abs($median) + 0.5);
            if (abs($sv[$i] - $median) > $threshold) {
                $keep[$indices[$i]] = false;
            }
        }
    }
    $ot = []; $ov = []; $os = [];
    for ($i = 0; $i < $n; $i++) {
        if ($keep[$i]) { $ot[] = $times[$i]; $ov[] = $values[$i]; $os[] = $sources[$i]; }
    }
    return [$ot, $ov, $os];
}

/**
 * Source-balanced moving mean for plot data with overlapping multi-source points.
 *
 * For each unique input timestamp ``t``: collect samples in a ±$half_window_s
 * window, average within each contributing source, then mean across sources.
 * Per-source-mean-then-cross-source-mean gives every source equal weight
 * regardless of cadence — so a 5-min USGS feed and a 15-min NWS feed each
 * count once at the cross-source step instead of USGS dominating 7-to-3.
 *
 * Edge handling: when ``t`` is within $half_window_s of the latest input
 * sample, the forward half is truncated — past side stays at 15 min, future
 * side shrinks to whatever is available (one-sided at the boundary).
 */
function _gp_cross_source_mean(array $times, array $values, array $sources,
                               int $half_window_s = 900): array {
    $n = count($times);
    if ($n < 2) return [$times, $values];
    // Sort by time, keeping (t, v, s) aligned.
    $idx = range(0, $n - 1);
    usort($idx, fn($a, $b) => $times[$a] <=> $times[$b]);
    $st = []; $sv = []; $ss = [];
    foreach ($idx as $i) {
        $st[] = $times[$i]; $sv[] = $values[$i]; $ss[] = $sources[$i];
    }
    $latest_t = $st[$n - 1];

    // Unique output timestamps in order.
    $out_t = []; $prev = null;
    foreach ($st as $t) { if ($t !== $prev) { $out_t[] = $t; $prev = $t; } }

    $out_v = [];
    foreach ($out_t as $t) {
        $window_start = $t - $half_window_s;
        $window_end   = ($latest_t - $t < $half_window_s) ? $t : $t + $half_window_s;
        $lo = _gp_lower_bound($st, $window_start);
        $sums = []; $cnts = []; // sid => float, int
        for ($i = $lo; $i < $n && $st[$i] <= $window_end; $i++) {
            $sid = $ss[$i];
            $sums[$sid] = ($sums[$sid] ?? 0.0) + $sv[$i];
            $cnts[$sid] = ($cnts[$sid] ?? 0) + 1;
        }
        if (empty($sums)) continue;
        $sum_of_means = 0.0; $n_sources = 0;
        foreach ($sums as $sid => $s) { $sum_of_means += $s / $cnts[$sid]; $n_sources++; }
        $out_v[] = $sum_of_means / $n_sources;
    }
    return [$out_t, $out_v];
}

/** Fetch [times[], values[]] for one data_type in the visible window.
 *
 * When the gauge has 2+ sources contributing to this data_type (USGS+NWS
 * fanout from the source split), each source's stream is despiked
 * (Hampel filter, 30-min window) and then averaged across sources
 * (per-source mean, then equal-weight mean across sources) to suppress
 * quarter-hour zigzag from disagreeing rating curves while keeping the
 * line at the midpoint of the two feeds. Single-source data is returned
 * verbatim.
 */
function _gp_fetch_series(PDO $db, int $gauge_id, string $type, string $since, ?string $until): array {
    if ($until !== null) {
        $stmt = $db->prepare(
            'SELECT o.observed_at, o.value, o.source_id FROM observation o
             JOIN gauge_source gs ON o.source_id = gs.source_id
             WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ? AND o.observed_at <= ?
             ORDER BY o.observed_at'
        );
        $stmt->execute([$gauge_id, $type, $since, $until]);
    } else {
        $stmt = $db->prepare(
            'SELECT o.observed_at, o.value, o.source_id FROM observation o
             JOIN gauge_source gs ON o.source_id = gs.source_id
             WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ?
             ORDER BY o.observed_at'
        );
        $stmt->execute([$gauge_id, $type, $since]);
    }
    $times = []; $values = []; $sources = [];
    foreach ($stmt->fetchAll() as $r) {
        $times[]   = strtotime($r['observed_at']);
        $values[]  = (float)$r['value'];
        $sources[] = (int)$r['source_id'];
    }
    if (count(array_unique($sources)) >= 2) {
        [$times, $values, $sources] = _gp_despike_per_source($times, $values, $sources);
        return _gp_cross_source_mean($times, $values, $sources);
    }
    return [$times, $values];
}

/** Emit one single-axis plot div, or nothing if the series has <2 points. */
function _gp_render_single_plot(array $times, array $values, string $name, string $y_label, bool $is_flow, ?array $bands = null): void {
    if (count($times) < 2) return;
    $title = htmlspecialchars($name) . " — $y_label";
    $svg = generate_svg_plot($times, $values, $title, $y_label, 800, 350, 200, $is_flow, $bands);
    echo '<div class="plot-container">' . $svg . '</div>';
}

/**
 * Project a reach class_range row onto a plot's y-axis.
 *
 * Returns ['low' => ?float, 'high' => ?float] in $axis_type units, or null if
 * the row is empty / a bound's data_type can't be converted (e.g. gauge bound
 * but no rating lookup is available for a flow plot).
 */
function _gp_bands_for_axis(?array $class_range, string $axis_type, ?array $rating_lookup = null): ?array {
    if ($class_range === null) return null;
    $axis_is_flow = ($axis_type === 'flow' || $axis_type === 'inflow');

    $project = function ($v, ?string $dt) use ($axis_is_flow, $rating_lookup): ?float {
        if ($v === null) return null;
        $v = (float)$v;
        $dt = $dt ?: 'flow';
        $bound_is_flow = ($dt === 'flow' || $dt === 'inflow');
        if ($bound_is_flow === $axis_is_flow) return $v;
        if ($rating_lookup === null) return null;
        return $axis_is_flow
            ? rate_gauge_to_flow($rating_lookup, $v)
            : rate_flow_to_gauge($rating_lookup, $v);
    };

    $lo = $project($class_range['low'] ?? null,  $class_range['low_data_type']  ?? null);
    $hi = $project($class_range['high'] ?? null, $class_range['high_data_type'] ?? null);
    if ($lo === null && $hi === null) return null;
    return ['low' => $lo, 'high' => $hi];
}

/**
 * Resolve the visible time window for a gauge.
 *
 * If $start_date and $end_date are both set, use them. Otherwise default
 * to the 10-day window ending at the gauge's latest observation (or now
 * if the gauge has no data).
 *
 * @return array{0:int,1:string,2:?string,3:bool}  [$latest_ts, $since, $until, $is_default_view]
 */
function gp_resolve_window(PDO $db, int $gauge_id, ?string $start_date, ?string $end_date): array {
    $stmt = $db->prepare(
        'SELECT MAX(o.observed_at) AS latest FROM observation o
         JOIN gauge_source gs ON o.source_id = gs.source_id
         WHERE gs.gauge_id = ?'
    );
    $stmt->execute([$gauge_id]);
    $row = $stmt->fetch();
    $latest_ts = $row && $row['latest'] ? strtotime($row['latest']) : time();

    if ($start_date && $end_date) {
        $since = date('Y-m-d 00:00:00', strtotime($start_date));
        $until = date('Y-m-d 23:59:59', strtotime($end_date));
        $is_default_view = false;
    } else {
        $since = date('Y-m-d H:i:s', $latest_ts - 10 * 86400);
        $until = null;
        $is_default_view = true;
    }
    return [$latest_ts, $since, $until, $is_default_view];
}

/**
 * Emit the shared date-range form (method=get, submits to the current page).
 *
 * @param int         $id           Always emitted as a hidden `id` input.
 * @param ?string     $start_date   Current value or null → default from $latest_ts.
 * @param ?string     $end_date     Current value or null → default from $latest_ts.
 * @param int         $latest_ts    Used to compute default start/end when unset.
 * @param array       $extra_links  [[label, url], ...] rendered after the Update button.
 */
function gp_render_date_form(
    int $id,
    ?string $start_date,
    ?string $end_date,
    int $latest_ts,
    array $extra_links = []
): void {
    $default_end = date('Y-m-d', $latest_ts);
    $default_start = date('Y-m-d', $latest_ts - 10 * 86400);
    $form_start = $start_date ?: $default_start;
    $form_end = $end_date ?: $default_end;

    echo '<form method="get" style="margin:.5rem 0;font-size:.85rem;display:flex;align-items:center;flex-wrap:wrap;gap:.5rem">';
    echo '<input type="hidden" name="id" value="' . $id . '">';
    echo '<label style="display:inline-flex;align-items:center;gap:.3rem;min-height:44px">Start: <input type="date" name="start" value="' . htmlspecialchars($form_start) . '" style="min-height:44px;padding:4px 8px"></label>';
    echo '<label style="display:inline-flex;align-items:center;gap:.3rem;min-height:44px">End: <input type="date" name="end" value="' . htmlspecialchars($form_end) . '" style="min-height:44px;padding:4px 8px"></label>';
    echo '<button type="submit" style="min-height:44px;padding:8px 16px">Update</button>';
    foreach ($extra_links as $link) {
        $url = htmlspecialchars($link['url']);
        $label = htmlspecialchars($link['label']);
        echo '<a href="' . $url . '" style="display:inline-flex;align-items:center;min-height:44px">' . $label . '</a>';
    }
    echo '</form>';
}

/**
 * Emit flow/gauge/temperature plots for a gauge in the resolved window.
 *
 * Decision tree (unchanged from description.php's historical behaviour):
 *   1. Primary = flow if available, else inflow.
 *      In default view, require the latest flow/inflow within 6h.
 *   2. If primary + gauge both present AND a rating lookup is derivable,
 *      render the dual-axis flow+gauge plot.
 *   3. Else, fall back to single-axis plots for each available series.
 *   4. Always append a temperature plot if data exists.
 */
function gp_render_plots(
    PDO $db,
    int $gauge_id,
    string $title_name,
    string $since,
    ?string $until,
    int $latest_ts,
    bool $is_default_view,
    ?array $class_range = null
): void {
    $has_flow   = _gp_has_obs($db, $gauge_id, 'flow',        $since, $until);
    $has_inflow = _gp_has_obs($db, $gauge_id, 'inflow',      $since, $until);
    $has_gauge  = _gp_has_obs($db, $gauge_id, 'gauge',       $since, $until);
    $has_temp   = _gp_has_obs($db, $gauge_id, 'temperature', $since, $until);

    if ($is_default_view) {
        $flow_current   = _gp_has_current_obs($db, $gauge_id, 'flow',   6);
        $inflow_current = _gp_has_current_obs($db, $gauge_id, 'inflow', 6);
        $primary_type = $flow_current ? 'flow' : ($inflow_current ? 'inflow' : null);
    } else {
        $primary_type = $has_flow ? 'flow' : ($has_inflow ? 'inflow' : null);
    }
    $primary_label = $primary_type === 'flow'   ? 'Flow (CFS)'
                   : ($primary_type === 'inflow' ? 'Inflow (CFS)' : '');

    if ($primary_type !== null && $has_gauge) {
        // Wider lookback for the rating curve than the visible window — axis
        // ticks should cover the plotted y-range even if the visible window
        // itself has few paired points.
        $lookup_since = date('Y-m-d H:i:s', $latest_ts - 60 * 86400);
        $lookup = derive_rating_lookup($db, $gauge_id, $primary_type, $lookup_since);

        [$ft, $fv] = _gp_fetch_series($db, $gauge_id, $primary_type, $since, $until);
        if ($lookup !== null && count($ft) >= 2) {
            $title = htmlspecialchars($title_name) . " — $primary_label / Gage Height";
            $flow_bands = _gp_bands_for_axis($class_range, $primary_type, $lookup);
            echo '<div class="plot-container">'
               . generate_rating_dual_plot($ft, $fv, $lookup, $title, $primary_label, 800, 350, 200, $flow_bands)
               . '</div>';
        } else {
            $flow_bands = _gp_bands_for_axis($class_range, $primary_type);
            _gp_render_single_plot($ft, $fv, $title_name, $primary_label, $primary_type === 'flow', $flow_bands);
            [$gt, $gv] = _gp_fetch_series($db, $gauge_id, 'gauge', $since, $until);
            $gauge_bands = _gp_bands_for_axis($class_range, 'gauge');
            _gp_render_single_plot($gt, $gv, $title_name, 'Gage Height (Ft)', false, $gauge_bands);
        }
    } elseif ($primary_type !== null) {
        [$ft, $fv] = _gp_fetch_series($db, $gauge_id, $primary_type, $since, $until);
        $flow_bands = _gp_bands_for_axis($class_range, $primary_type);
        _gp_render_single_plot($ft, $fv, $title_name, $primary_label, $primary_type === 'flow', $flow_bands);
    } elseif ($has_gauge) {
        [$gt, $gv] = _gp_fetch_series($db, $gauge_id, 'gauge', $since, $until);
        $gauge_bands = _gp_bands_for_axis($class_range, 'gauge');
        _gp_render_single_plot($gt, $gv, $title_name, 'Gage Height (Ft)', false, $gauge_bands);
    }

    if ($has_temp) {
        [$tt, $tv] = _gp_fetch_series($db, $gauge_id, 'temperature', $since, $until);
        _gp_render_single_plot($tt, $tv, $title_name, 'Temperature (F)', false);
    }
}
