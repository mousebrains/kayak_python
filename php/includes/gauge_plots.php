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

/** Fetch [times[], values[]] for one data_type in the visible window. */
function _gp_fetch_series(PDO $db, int $gauge_id, string $type, string $since, ?string $until): array {
    if ($until !== null) {
        $stmt = $db->prepare(
            'SELECT o.observed_at, o.value FROM observation o
             JOIN gauge_source gs ON o.source_id = gs.source_id
             WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ? AND o.observed_at <= ?
             ORDER BY o.observed_at'
        );
        $stmt->execute([$gauge_id, $type, $since, $until]);
    } else {
        $stmt = $db->prepare(
            'SELECT o.observed_at, o.value FROM observation o
             JOIN gauge_source gs ON o.source_id = gs.source_id
             WHERE gs.gauge_id = ? AND o.data_type = ? AND o.observed_at >= ?
             ORDER BY o.observed_at'
        );
        $stmt->execute([$gauge_id, $type, $since]);
    }
    $times = []; $values = [];
    foreach ($stmt->fetchAll() as $r) {
        $times[]  = strtotime($r['observed_at']);
        $values[] = (float)$r['value'];
    }
    return [$times, $values];
}

/** Emit one single-axis plot div, or nothing if the series has <2 points. */
function _gp_render_single_plot(array $times, array $values, string $name, string $y_label, bool $is_flow): void {
    if (count($times) < 2) return;
    $title = htmlspecialchars($name) . " — $y_label";
    $svg = generate_svg_plot($times, $values, $title, $y_label, 800, 350, 200, $is_flow);
    echo '<div class="plot-container">' . $svg . '</div>';
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
    bool $is_default_view
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
            echo '<div class="plot-container">'
               . generate_rating_dual_plot($ft, $fv, $lookup, $title, $primary_label)
               . '</div>';
        } else {
            _gp_render_single_plot($ft, $fv, $title_name, $primary_label, $primary_type === 'flow');
            [$gt, $gv] = _gp_fetch_series($db, $gauge_id, 'gauge', $since, $until);
            _gp_render_single_plot($gt, $gv, $title_name, 'Gage Height (Ft)', false);
        }
    } elseif ($primary_type !== null) {
        [$ft, $fv] = _gp_fetch_series($db, $gauge_id, $primary_type, $since, $until);
        _gp_render_single_plot($ft, $fv, $title_name, $primary_label, $primary_type === 'flow');
    } elseif ($has_gauge) {
        [$gt, $gv] = _gp_fetch_series($db, $gauge_id, 'gauge', $since, $until);
        _gp_render_single_plot($gt, $gv, $title_name, 'Gage Height (Ft)', false);
    }

    if ($has_temp) {
        [$tt, $tv] = _gp_fetch_series($db, $gauge_id, 'temperature', $since, $until);
        _gp_render_single_plot($tt, $tv, $title_name, 'Temperature (F)', false);
    }
}
