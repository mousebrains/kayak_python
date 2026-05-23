<?php
declare(strict_types=1);

require_once __DIR__ . '/lttb.php';
// Rating-curve interpolation lives in svg_plot_rating.php — extracted in
// Tier 4.3 of php_layer_split. `generate_rating_dual_plot` below calls
// rate_*_to_* internally, so we require it here.
require_once __DIR__ . '/svg_plot_rating.php';

/**
 * Split "Label (Unit)" into [label, unit]. Returns [$y_label, ''] if no parens.
 * @return array{0: string, 1: string}
 */
function _split_y_label(string $y_label): array {
    if (preg_match('/^(.+?)\s*\(([^)]+)\)\s*$/', $y_label, $m)) {
        return [$m[1], $m[2]];
    }
    return [$y_label, ''];
}

/**
 * JSON-encode a series payload for a data-series="..." SVG attribute.
 * HTML-escapes for safe interpolation inside double-quoted attribute.
 *
 * @param array<int|string, mixed> $series
 */
function _series_data_attr(array $series): string {
    $json = json_encode($series, JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);
    if ($json === false) return '';
    return htmlspecialchars($json);
}

/**
 * Compute nice Y-axis bounds and step for round tick labels.
 *
 * @return array{0: float, 1: float, 2: float} [$y_min, $y_max, $step]
 */
function nice_axis(float $data_min, float $data_max): array {
    $range = $data_max - $data_min;
    if ($range < 1e-9) $range = 1.0;

    // Find magnitude and candidate steps
    $mag = pow(10, floor(log10($range)));
    $candidates = [5 * $mag, 2 * $mag, 1 * $mag, 0.5 * $mag, 0.2 * $mag, 0.1 * $mag];

    foreach ($candidates as $step) {
        $lo = floor($data_min / $step) * $step;
        $hi = ceil($data_max / $step) * $step;
        $n_ticks = round(($hi - $lo) / $step);
        if ($n_ticks >= 4 && $n_ticks <= 8) {
            return [$lo, $hi, $step];
        }
    }

    // Fallback: use the candidate closest to 5 ticks
    $step = $candidates[0];
    foreach ($candidates as $s) {
        $lo = floor($data_min / $s) * $s;
        $hi = ceil($data_max / $s) * $s;
        $n = round(($hi - $lo) / $s);
        if ($n >= 3 && $n <= 10) {
            $step = $s;
            break;
        }
    }
    $lo = floor($data_min / $step) * $step;
    $hi = ceil($data_max / $step) * $step;
    return [$lo, $hi, $step];
}

/**
 * Render low/okay/high background bands (in axis units) clipped to the
 * visible y-range. Bands are decorative — they never extend the y-axis.
 *
 * @param array{low?: ?float, high?: ?float}|null $bands  Axis-unit bounds, or null.
 */
function _bands_svg(?array $bands, float $y_min, float $y_max, int $ml, int $mt, int $pw, int $ph): string {
    if ($bands === null) return '';
    $lo = $bands['low'] ?? null;
    $hi = $bands['high'] ?? null;
    if ($lo === null && $hi === null) return '';

    // Zones in data coords: [bot_data, top_data].
    $zones = [];
    if ($lo !== null && $hi !== null) {
        $zones[] = ['level' => 'low',  'bot' => $y_min, 'top' => $lo];
        $zones[] = ['level' => 'okay', 'bot' => $lo,    'top' => $hi];
        $zones[] = ['level' => 'high', 'bot' => $hi,    'top' => $y_max];
    } elseif ($lo !== null) {
        $zones[] = ['level' => 'low',  'bot' => $y_min, 'top' => $lo];
        $zones[] = ['level' => 'okay', 'bot' => $lo,    'top' => $y_max];
    } else { // $hi !== null
        $zones[] = ['level' => 'okay', 'bot' => $y_min, 'top' => $hi];
        $zones[] = ['level' => 'high', 'bot' => $hi,    'top' => $y_max];
    }

    $colors = ['low' => '#e8a735', 'okay' => '#4caf50', 'high' => '#e53935'];
    $y_range = $y_max - $y_min ?: 1;
    $svg = '';
    foreach ($zones as $z) {
        $top = max($y_min, min($y_max, (float)$z['top']));
        $bot = max($y_min, min($y_max, (float)$z['bot']));
        if ($top <= $bot) continue;
        $py_top = $mt + (int)(($y_max - $top) / $y_range * $ph);
        $py_bot = $mt + (int)(($y_max - $bot) / $y_range * $ph);
        $h = $py_bot - $py_top;
        if ($h <= 0) continue;
        $color = $colors[$z['level']];
        $svg .= "<rect x=\"$ml\" y=\"$py_top\" width=\"$pw\" height=\"$h\" fill=\"$color\" fill-opacity=\"0.12\"/>\n";
    }
    return $svg;
}

/**
 * Generate a lightweight time-series SVG plot.
 *
 * @param list<int>                               $times          Unix timestamps.
 * @param list<float>                             $values         Float values.
 * @param string                                  $title          Plot title.
 * @param string                                  $y_label        Y-axis label.
 * @param int                                     $width          SVG width.
 * @param int                                     $height         SVG height.
 * @param int                                     $target_points  LTTB target.
 * @param bool                                    $is_flow        Whether Y-axis values are flow (integer labels).
 * @param array{low?: ?float, high?: ?float}|null $bands          Axis-unit band bounds.
 */
function generate_svg_plot(
    array $times,
    array $values,
    string $title,
    string $y_label,
    int $width = 800,
    int $height = 350,
    int $target_points = 200,
    bool $is_flow = false,
    ?array $bands = null
): string {
    $n = count($times);
    if ($n === 0) {
        return _empty_svg($title, $width, $height);
    }

    // Build pairs and sort by time
    $pairs = [];
    for ($i = 0; $i < $n; $i++) {
        if ($values[$i] !== null) {
            $pairs[] = [(float)$times[$i], (float)$values[$i]];
        }
    }
    usort($pairs, fn($a, $b) => $a[0] <=> $b[0]);

    if (count($pairs) < 2) {
        return _empty_svg($title, $width, $height);
    }

    // Downsample
    $pairs = lttb_downsample($pairs, $target_points);

    // Margins
    $ml = 80; $mr = 20; $mt = 30; $mb = 45;
    $pw = $width - $ml - $mr;   // plot width
    $ph = $height - $mt - $mb;  // plot height

    // Ranges
    $x_min = $pairs[0][0];
    $x_max = $pairs[count($pairs) - 1][0];
    $y_vals = array_column($pairs, 1);
    [$y_min, $y_max, $y_step] = nice_axis(min($y_vals), max($y_vals));
    $x_range = $x_max - $x_min ?: 1;
    $y_range = $y_max - $y_min ?: 1;

    // Map to pixel coords
    $points_str = '';
    foreach ($pairs as [$x, $y]) {
        $px = $ml + (int)(($x - $x_min) / $x_range * $pw);
        $py = $mt + (int)(($y_max - $y) / $y_range * $ph);
        $points_str .= "$px,$py ";
    }
    $points_str = rtrim($points_str);

    // Y-axis grid lines and labels
    $grid = '';
    $y_decimals = $y_step >= 1 ? 0 : ($y_step >= 0.1 ? 1 : 2);
    for ($yv = $y_min; $yv <= $y_max + $y_step * 0.01; $yv += $y_step) {
        $py = $mt + (int)(($y_max - $yv) / $y_range * $ph);
        $label = number_format($yv, $y_decimals);
        $grid .= "<line x1=\"$ml\" y1=\"$py\" x2=\"" . ($ml + $pw) . "\" y2=\"$py\" stroke=\"#ddd\" stroke-width=\"0.5\"/>\n";
        $grid .= "<text x=\"" . ($ml - 5) . "\" y=\"" . ($py + 4) . "\" text-anchor=\"end\" font-size=\"14\" fill=\"#666\">$label</text>\n";
    }

    // X-axis date labels
    $span_days = ($x_max - $x_min) / 86400;
    $n_xticks = $span_days > 14 ? 5 : ($span_days > 3 ? (int)$span_days : 6);
    $n_xticks = max($n_xticks, 2);
    for ($i = 0; $i <= $n_xticks; $i++) {
        $xv = $x_min + ($x_range * $i / $n_xticks);
        $px = $ml + (int)(($xv - $x_min) / $x_range * $pw);
        $label = $span_days > 3 ? date('n/j', (int)$xv) : date('n/j H:i', (int)$xv);
        $grid .= "<line x1=\"$px\" y1=\"$mt\" x2=\"$px\" y2=\"" . ($mt + $ph) . "\" stroke=\"#ddd\" stroke-width=\"0.5\"/>\n";
        $grid .= "<text x=\"$px\" y=\"" . ($height - 8) . "\" text-anchor=\"middle\" font-size=\"14\" fill=\"#666\">$label</text>\n";
    }

    $esc_title = htmlspecialchars($title);
    $esc_ylabel = htmlspecialchars($y_label);
    $series_attr = _series_data_attr([
        'kind'     => 'single',
        'points'   => array_map(fn($p) => [(int)$p[0], $p[1]], $pairs),
        'label'    => _split_y_label($y_label)[0],
        'unit'     => _split_y_label($y_label)[1],
        'decimals' => $y_decimals,
        'y_min'    => $y_min,
        'y_max'    => $y_max,
        'margins'  => ['ml' => $ml, 'mr' => $mr, 'mt' => $mt, 'mb' => $mb, 'w' => $width, 'h' => $height],
    ]);

    $bands_svg = _bands_svg($bands, (float)$y_min, (float)$y_max, $ml, $mt, $pw, $ph);

    return <<<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 $width $height" width="$width" height="$height" data-series="$series_attr">
<text x="{$ml}" y="18" font-size="13" font-weight="bold" fill="#333">$esc_title</text>
<text x="12" y="{$mt}" font-size="10" fill="#666" transform="rotate(-90,12,{$mt})" text-anchor="end">$esc_ylabel</text>
$bands_svg$grid
<rect x="$ml" y="$mt" width="$pw" height="$ph" fill="none" stroke="#ccc" stroke-width="0.5"/>
<polyline fill="none" stroke="#2060A0" stroke-width="1.5" stroke-linejoin="round" points="$points_str"/>
</svg>
SVG;
}

/**
 * Dual-axis plot: one flow line, left axis linear (CFS), right axis is a
 * rating-curve re-labelling of the same Y coordinate (gage ft). The right-axis
 * ticks land on "nice" gauge-height values and are placed at the y-pixel that
 * maps to that gauge's flow through $rating_lookup.
 *
 * @param list<int>                               $flow_times      Unix timestamps.
 * @param list<float>                             $flow_values     Flow values (CFS).
 * @param array<int, array{0: float, 1: float}>   $rating_lookup   Sorted (gauge_ft, flow_cfs) pairs.
 * @param string                                  $title           Plot title.
 * @param string                                  $primary_label   Left-axis label.
 * @param array{low?: ?float, high?: ?float}|null $bands           Axis-unit band bounds.
 */
function generate_rating_dual_plot(
    array $flow_times,
    array $flow_values,
    array $rating_lookup,
    string $title,
    string $primary_label,
    int $width = 800,
    int $height = 350,
    int $target_points = 200,
    ?array $bands = null
): string {
    $n = count($flow_times);
    if ($n === 0) return _empty_svg($title, $width, $height);

    $pairs = [];
    for ($i = 0; $i < $n; $i++) {
        if ($flow_values[$i] !== null) {
            $pairs[] = [(float)$flow_times[$i], (float)$flow_values[$i]];
        }
    }
    usort($pairs, fn($a, $b) => $a[0] <=> $b[0]);
    if (count($pairs) < 2) return _empty_svg($title, $width, $height);

    $pairs = lttb_downsample($pairs, $target_points);

    $ml = 80; $mr = 80; $mt = 30; $mb = 45;
    $pw = $width - $ml - $mr;
    $ph = $height - $mt - $mb;

    $x_min = $pairs[0][0];
    $x_max = $pairs[count($pairs) - 1][0];
    $fy_vals = array_column($pairs, 1);
    [$fy_min, $fy_max, $fy_step] = nice_axis(min($fy_vals), max($fy_vals));
    $x_range = $x_max - $x_min ?: 1;
    $fy_range = $fy_max - $fy_min ?: 1;

    // Flow polyline
    $pts = '';
    foreach ($pairs as [$x, $y]) {
        $px = $ml + (int)(($x - $x_min) / $x_range * $pw);
        $py = $mt + (int)(($fy_max - $y) / $fy_range * $ph);
        $pts .= "$px,$py ";
    }
    $flow_line = '<polyline fill="none" stroke="#2060A0" stroke-width="1.5" stroke-linejoin="round" points="' . rtrim($pts) . '"/>';

    // Left-axis (flow) gridlines + labels
    $grid = '';
    $fy_decimals = $fy_step >= 1 ? 0 : ($fy_step >= 0.1 ? 1 : 2);
    for ($yv = $fy_min; $yv <= $fy_max + $fy_step * 0.01; $yv += $fy_step) {
        $py = $mt + (int)(($fy_max - $yv) / $fy_range * $ph);
        $label = number_format($yv, $fy_decimals);
        $grid .= "<line x1=\"$ml\" y1=\"$py\" x2=\"" . ($ml + $pw) . "\" y2=\"$py\" stroke=\"#ddd\" stroke-width=\"0.5\"/>\n";
        $grid .= "<text x=\"" . ($ml - 5) . "\" y=\"" . ($py + 4) . "\" text-anchor=\"end\" font-size=\"14\" fill=\"#2060A0\">$label</text>\n";
    }

    // Right-axis: nice gauge-height tick values, placed at y-pixel of their rated flow.
    $right_grid = '';
    $right_x = $ml + $pw;
    $gy_visible_min = rate_flow_to_gauge($rating_lookup, (float)$fy_min);
    $gy_visible_max = rate_flow_to_gauge($rating_lookup, (float)$fy_max);
    if ($gy_visible_min !== null && $gy_visible_max !== null) {
        $lo = min($gy_visible_min, $gy_visible_max);
        $hi = max($gy_visible_min, $gy_visible_max);
        [$gy_min, $gy_max, $gy_step] = nice_axis($lo, $hi);
        $gy_decimals = $gy_step >= 1 ? 0 : ($gy_step >= 0.1 ? 1 : 2);

        $valid_ticks = [];
        for ($gv = $gy_min; $gv <= $gy_max + $gy_step * 0.01; $gv += $gy_step) {
            $qv = rate_gauge_to_flow($rating_lookup, $gv);
            if ($qv === null || $qv < $fy_min || $qv > $fy_max) continue;
            $valid_ticks[] = [$gv, $qv];
        }
        // Fallback: if no nice tick landed in-range, place labels at visible endpoints.
        if (!$valid_ticks) {
            foreach ([$lo, $hi] as $gv) {
                $qv = rate_gauge_to_flow($rating_lookup, $gv);
                if ($qv !== null) $valid_ticks[] = [$gv, $qv];
            }
        }
        foreach ($valid_ticks as [$gv, $qv]) {
            $py = $mt + (int)(($fy_max - $qv) / $fy_range * $ph);
            $label = number_format($gv, $gy_decimals);
            $right_grid .= "<line x1=\"$right_x\" y1=\"$py\" x2=\"" . ($right_x + 3) . "\" y2=\"$py\" stroke=\"#C04020\" stroke-width=\"0.5\"/>\n";
            $right_grid .= "<text x=\"" . ($right_x + 5) . "\" y=\"" . ($py + 4) . "\" text-anchor=\"start\" font-size=\"14\" fill=\"#C04020\">$label</text>\n";
        }
    }

    // X-axis date labels
    $x_labels = '';
    $span_days = ($x_max - $x_min) / 86400;
    $n_xticks = $span_days > 14 ? 5 : ($span_days > 3 ? (int)$span_days : 6);
    $n_xticks = max($n_xticks, 2);
    for ($i = 0; $i <= $n_xticks; $i++) {
        $xv = $x_min + ($x_range * $i / $n_xticks);
        $px = $ml + (int)(($xv - $x_min) / $x_range * $pw);
        $label = $span_days > 3 ? date('n/j', (int)$xv) : date('n/j H:i', (int)$xv);
        $x_labels .= "<line x1=\"$px\" y1=\"$mt\" x2=\"$px\" y2=\"" . ($mt + $ph) . "\" stroke=\"#ddd\" stroke-width=\"0.5\"/>\n";
        $x_labels .= "<text x=\"$px\" y=\"" . ($height - 8) . "\" text-anchor=\"middle\" font-size=\"14\" fill=\"#666\">$label</text>\n";
    }

    $esc_title = htmlspecialchars($title);
    $esc_flow_label = htmlspecialchars($primary_label);
    $gauge_label_x = $width - 12;
    $series_attr = _series_data_attr([
        'kind'           => 'dual',
        'points'         => array_map(fn($p) => [(int)$p[0], $p[1]], $pairs),
        'label'          => _split_y_label($primary_label)[0],
        'unit'           => _split_y_label($primary_label)[1],
        'decimals'       => $fy_decimals,
        'y_min'          => $fy_min,
        'y_max'          => $fy_max,
        'rating'         => $rating_lookup,
        'gauge_decimals' => 1,
        'margins'        => ['ml' => $ml, 'mr' => $mr, 'mt' => $mt, 'mb' => $mb, 'w' => $width, 'h' => $height],
    ]);

    $bands_svg = _bands_svg($bands, (float)$fy_min, (float)$fy_max, $ml, $mt, $pw, $ph);

    return <<<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 $width $height" width="$width" height="$height" data-series="$series_attr">
<text x="{$ml}" y="18" font-size="13" font-weight="bold" fill="#333">$esc_title</text>
<text x="12" y="{$mt}" font-size="11" fill="#2060A0" transform="rotate(-90,12,{$mt})" text-anchor="end">$esc_flow_label</text>
<text x="{$gauge_label_x}" y="{$mt}" font-size="11" fill="#C04020" transform="rotate(90,{$gauge_label_x},{$mt})" text-anchor="end">Gage Height (Ft)</text>
$bands_svg$grid
$right_grid
$x_labels
<rect x="$ml" y="$mt" width="$pw" height="$ph" fill="none" stroke="#ccc" stroke-width="0.5"/>
$flow_line
</svg>
SVG;
}

/**
 * Render a gradient profile as a continuous SVG line chart.
 *
 * Reads the JSON produced by scripts/compute_reach_gradient.py
 * (shape documented in data/db/migrations/0045_*.sql header). Draws
 * one pale full-opacity polyline for all samples, then overlays
 * disjoint full-color polylines for each contiguous "significant: true"
 * run, so insignificant stretches show as dimmed. Embeds the sample
 * array as a data-profile attribute for static/gradient-profile.js to
 * read at hydration time (cursor sync with the reach map dot).
 *
 * Returns '' if the profile JSON is empty, malformed, or has fewer than
 * 2 samples.
 */
function generate_gradient_profile_svg(
    string $profile_json,
    int $reach_id,
    int $width = 900,
    int $height = 180
): string {
    if ($profile_json === '') return '';
    $data = json_decode($profile_json, true);
    if (!is_array($data) || !isset($data['samples']) || !is_array($data['samples'])) {
        return '';
    }
    $samples = $data['samples'];
    if (count($samples) < 2) return '';

    // viewBox dimensions (responsive — CSS sets actual rendered width to 100%
    // of container). Margins (tighter than generate_svg_plot since this is a
    // sub-widget).
    $ml = 50; $mr = 10; $mt = 22; $mb = 26;
    $pw = $width - $ml - $mr;
    $ph = $height - $mt - $mb;

    // Ranges
    $x_min = (float)$samples[0]['d_mi'];
    $x_max = (float)$samples[count($samples) - 1]['d_mi'];
    $x_range = $x_max - $x_min ?: 1;
    $y_vals = array_map(fn($s) => (float)$s['grad_ft_per_mi'], $samples);
    // Anchor y-axis at zero so a short bar (low gradient) reads short
    // and a tall bar reads tall. nice_axis() handles only the top end.
    [, $y_max, $y_step] = nice_axis(0.0, max($y_vals));
    $y_min = 0.0;
    $y_range = $y_max - $y_min ?: 1;

    // Bar plot: each sample renders as a rect spanning its 3-sigma
    // ANALYSIS window (d_mi ± w_mi/2) at height = grad_ft_per_mi.
    // Bars overlap when consecutive samples have wider windows than
    // step_mi (the usual case). Semi-transparent fill makes overlap
    // density visible — narrow windows (0.0625 mi, where the gradient
    // is steep enough that fine resolution is statistically meaningful)
    // render as crisp distinct bars; wide windows (0.5-5 mi, where
    // the algorithm had to integrate further to clear the noise floor)
    // render as broader semi-transparent rectangles whose overlaps
    // build up darker regions.
    //
    // Bars sorted by w_mi descending so wider bars draw first
    // (background), narrower windows draw on top — peaks read clearly.
    $xPx_per_mi = $pw / $x_range;
    $yPx_per_grad = $ph / $y_range;
    $plot_right = $ml + $pw;
    $plot_bottom = $mt + $ph;

    $ordered = $samples;
    usort($ordered, fn($a, $b) => (float)$b['w_mi'] <=> (float)$a['w_mi']);

    $bars_pale = '';
    $bars_sig = '';
    foreach ($ordered as $s) {
        $d_mi = (float)$s['d_mi'];
        $w_mi = (float)$s['w_mi'];
        $grad = max(0.0, (float)$s['grad_ft_per_mi']);
        $sig = !empty($s['significant']);

        $left_x = $ml + (($d_mi - $w_mi / 2) - $x_min) * $xPx_per_mi;
        $right_x = $ml + (($d_mi + $w_mi / 2) - $x_min) * $xPx_per_mi;
        $left_x = max($ml, $left_x);
        $right_x = min($plot_right, $right_x);
        $bar_w = $right_x - $left_x;
        if ($bar_w < 0.1) continue;

        $top_y = $mt + ($y_max - $grad) * $yPx_per_grad;
        $top_y = max((float)$mt, min((float)$plot_bottom, $top_y));
        $bar_h = $plot_bottom - $top_y;
        if ($bar_h < 0.1) continue;

        $rect = sprintf('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f"/>', $left_x, $top_y, $bar_w, $bar_h);
        if ($sig) {
            $bars_sig .= $rect;
        } else {
            $bars_pale .= $rect;
        }
    }

    // Y-axis grid + labels
    $grid = '';
    for ($yv = $y_min; $yv <= $y_max + $y_step * 0.01; $yv += $y_step) {
        $py = $mt + (($y_max - $yv) / $y_range * $ph);
        $label = number_format($yv, 0);
        $grid .= "<line class=\"gp-grid\" x1=\"$ml\" y1=\"$py\" x2=\"" . ($ml + $pw) . "\" y2=\"$py\"/>\n";
        $grid .= "<text class=\"gp-axis\" x=\"" . ($ml - 5) . "\" y=\"" . ($py + 4) . "\" text-anchor=\"end\">$label</text>\n";
    }

    // X-axis ticks (every ~5 ticks). Pick label decimal precision from
    // the tick step so short reaches (Henline at 0.6 mi) don't repeat
    // their labels at 1-decimal rounding (0.0, 0.1, 0.2, 0.4, 0.5, 0.6).
    $n_xticks = 5;
    $x_tick_step = $x_range / $n_xticks;
    $x_decimals = $x_tick_step >= 1 ? 0 : ($x_tick_step >= 0.1 ? 1 : ($x_tick_step >= 0.01 ? 2 : 3));
    for ($i = 0; $i <= $n_xticks; $i++) {
        $xv = $x_min + ($x_range * $i / $n_xticks);
        $px = $ml + (($xv - $x_min) / $x_range * $pw);
        $label = number_format($xv, $x_decimals);
        $grid .= "<line class=\"gp-grid\" x1=\"$px\" y1=\"$mt\" x2=\"$px\" y2=\"" . ($mt + $ph) . "\"/>\n";
        $grid .= "<text class=\"gp-axis\" x=\"$px\" y=\"" . ($height - 8) . "\" text-anchor=\"middle\">$label</text>\n";
    }

    // Hydration payload (static/gradient-profile.js reads this)
    $payload = json_encode([
        'samples' => $samples,
        'x_min' => $x_min,
        'x_max' => $x_max,
        'y_min' => $y_min,
        'y_max' => $y_max,
        'margins' => ['ml' => $ml, 'mr' => $mr, 'mt' => $mt, 'mb' => $mb,
                      'pw' => $pw, 'ph' => $ph, 'w' => $width, 'h' => $height],
    ], JSON_UNESCAPED_SLASHES | JSON_PRESERVE_ZERO_FRACTION);
    if ($payload === false) {
        $payload = '{}';
    }
    $payload_attr = htmlspecialchars($payload);

    $title_x = (int)($width / 2);
    return <<<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 $width $height" class="gradient-profile-chart" data-reach-id="$reach_id" data-profile="$payload_attr" role="img" aria-label="Gradient profile chart">
<text class="gp-title" x="{$title_x}" y="16" text-anchor="middle">Gradient (ft/mi) vs. river mile</text>
$grid
<rect class="gp-frame" x="$ml" y="$mt" width="$pw" height="$ph"/>
<g class="gp-bars-pale">$bars_pale</g>
<g class="gp-bars-sig">$bars_sig</g>
</svg>
SVG;
}

function _empty_svg(string $title, int $width, int $height): string {
    $cx = (int)($width / 2);
    $cy = (int)($height / 2);
    $esc = htmlspecialchars($title);
    return <<<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 $width $height" width="$width" height="$height">
<text x="$cx" y="20" font-size="13" text-anchor="middle" fill="#333">$esc</text>
<text x="$cx" y="$cy" font-size="14" text-anchor="middle" fill="#999">No data available</text>
</svg>
SVG;
}
