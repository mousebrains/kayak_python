<?php
declare(strict_types=1);

require_once __DIR__ . '/lttb.php';

/**
 * Compute nice Y-axis bounds and step for round tick labels.
 *
 * @return array [$y_min, $y_max, $step]
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
 * Generate a lightweight time-series SVG plot.
 *
 * @param array  $times   Array of Unix timestamps.
 * @param array  $values  Array of float values.
 * @param string $title   Plot title.
 * @param string $y_label Y-axis label.
 * @param int    $width   SVG width.
 * @param int    $height  SVG height.
 * @param int    $target_points  LTTB target.
 * @param bool   $is_flow Whether Y-axis values are flow (integer labels).
 * @return string  SVG markup.
 */
function generate_svg_plot(
    array $times,
    array $values,
    string $title,
    string $y_label,
    int $width = 800,
    int $height = 350,
    int $target_points = 200,
    bool $is_flow = false
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

    return <<<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 $width $height" width="$width" height="$height">
<text x="{$ml}" y="18" font-size="13" font-weight="bold" fill="#333">$esc_title</text>
<text x="12" y="{$mt}" font-size="10" fill="#666" transform="rotate(-90,12,{$mt})" text-anchor="end">$esc_ylabel</text>
$grid
<rect x="$ml" y="$mt" width="$pw" height="$ph" fill="none" stroke="#ccc" stroke-width="0.5"/>
<polyline fill="none" stroke="#2060A0" stroke-width="1.5" stroke-linejoin="round" points="$points_str"/>
</svg>
SVG;
}

/**
 * Generate a dual-axis SVG plot with flow (left axis) and gauge height (right axis).
 *
 * @param array  $flow_times   Unix timestamps for flow data.
 * @param array  $flow_values  Flow values (CFS).
 * @param array  $gauge_times  Unix timestamps for gauge data.
 * @param array  $gauge_values Gauge height values (ft).
 * @param string $title        Plot title.
 * @param int    $width        SVG width.
 * @param int    $height       SVG height.
 * @param int    $target_points LTTB target per series.
 * @return string SVG markup.
 */
function generate_dual_svg_plot(
    array $flow_times,
    array $flow_values,
    array $gauge_times,
    array $gauge_values,
    string $title,
    int $width = 800,
    int $height = 350,
    int $target_points = 200
): string {
    // Build and sort pairs for each series
    $flow_pairs = [];
    for ($i = 0; $i < count($flow_times); $i++) {
        if ($flow_values[$i] !== null) {
            $flow_pairs[] = [(float)$flow_times[$i], (float)$flow_values[$i]];
        }
    }
    usort($flow_pairs, fn($a, $b) => $a[0] <=> $b[0]);

    $gauge_pairs = [];
    for ($i = 0; $i < count($gauge_times); $i++) {
        if ($gauge_values[$i] !== null) {
            $gauge_pairs[] = [(float)$gauge_times[$i], (float)$gauge_values[$i]];
        }
    }
    usort($gauge_pairs, fn($a, $b) => $a[0] <=> $b[0]);

    $has_flow = count($flow_pairs) >= 2;
    $has_gauge = count($gauge_pairs) >= 2;

    if (!$has_flow && !$has_gauge) {
        return _empty_svg($title, $width, $height);
    }

    // Downsample
    if ($has_flow) $flow_pairs = lttb_downsample($flow_pairs, $target_points);
    if ($has_gauge) $gauge_pairs = lttb_downsample($gauge_pairs, $target_points);

    // Margins — right margin wider for second Y axis
    $ml = 80; $mr = 80; $mt = 30; $mb = 45;
    $pw = $width - $ml - $mr;
    $ph = $height - $mt - $mb;

    // X range from combined data
    $all_x = [];
    if ($has_flow) { $all_x[] = $flow_pairs[0][0]; $all_x[] = $flow_pairs[count($flow_pairs)-1][0]; }
    if ($has_gauge) { $all_x[] = $gauge_pairs[0][0]; $all_x[] = $gauge_pairs[count($gauge_pairs)-1][0]; }
    $x_min = min($all_x);
    $x_max = max($all_x);
    $x_range = $x_max - $x_min ?: 1;

    // Flow Y axis (left)
    $flow_grid = '';
    $flow_line = '';
    if ($has_flow) {
        $fy_vals = array_column($flow_pairs, 1);
        [$fy_min, $fy_max, $fy_step] = nice_axis(min($fy_vals), max($fy_vals));
        $fy_range = $fy_max - $fy_min ?: 1;
        $fy_decimals = $fy_step >= 1 ? 0 : ($fy_step >= 0.1 ? 1 : 2);

        for ($yv = $fy_min; $yv <= $fy_max + $fy_step * 0.01; $yv += $fy_step) {
            $py = $mt + (int)(($fy_max - $yv) / $fy_range * $ph);
            $label = number_format($yv, $fy_decimals);
            $flow_grid .= "<line x1=\"$ml\" y1=\"$py\" x2=\"" . ($ml + $pw) . "\" y2=\"$py\" stroke=\"#ddd\" stroke-width=\"0.5\"/>\n";
            $flow_grid .= "<text x=\"" . ($ml - 5) . "\" y=\"" . ($py + 4) . "\" text-anchor=\"end\" font-size=\"14\" fill=\"#2060A0\">$label</text>\n";
        }

        $pts = '';
        foreach ($flow_pairs as [$x, $y]) {
            $px = $ml + (int)(($x - $x_min) / $x_range * $pw);
            $py = $mt + (int)(($fy_max - $y) / $fy_range * $ph);
            $pts .= "$px,$py ";
        }
        $flow_line = '<polyline fill="none" stroke="#2060A0" stroke-width="1.5" stroke-linejoin="round" points="' . rtrim($pts) . '"/>';
    }

    // Gauge Y axis (right)
    $gauge_grid = '';
    $gauge_line = '';
    if ($has_gauge) {
        $gy_vals = array_column($gauge_pairs, 1);
        [$gy_min, $gy_max, $gy_step] = nice_axis(min($gy_vals), max($gy_vals));
        $gy_range = $gy_max - $gy_min ?: 1;
        $gy_decimals = $gy_step >= 1 ? 0 : ($gy_step >= 0.1 ? 1 : 2);

        $right_x = $ml + $pw;
        for ($yv = $gy_min; $yv <= $gy_max + $gy_step * 0.01; $yv += $gy_step) {
            $py = $mt + (int)(($gy_max - $yv) / $gy_range * $ph);
            $label = number_format($yv, $gy_decimals);
            if (!$has_flow) {
                $gauge_grid .= "<line x1=\"$ml\" y1=\"$py\" x2=\"$right_x\" y2=\"$py\" stroke=\"#ddd\" stroke-width=\"0.5\"/>\n";
            }
            $gauge_grid .= "<text x=\"" . ($right_x + 5) . "\" y=\"" . ($py + 4) . "\" text-anchor=\"start\" font-size=\"14\" fill=\"#C04020\">$label</text>\n";
        }

        $pts = '';
        foreach ($gauge_pairs as [$x, $y]) {
            $px = $ml + (int)(($x - $x_min) / $x_range * $pw);
            $py = $mt + (int)(($gy_max - $y) / $gy_range * $ph);
            $pts .= "$px,$py ";
        }
        $gauge_line = '<polyline fill="none" stroke="#C04020" stroke-width="1.5" stroke-linejoin="round" points="' . rtrim($pts) . '"/>';
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

    // Y-axis labels
    $flow_label = $has_flow ? '<text x="12" y="' . $mt . '" font-size="11" fill="#2060A0" transform="rotate(-90,12,' . $mt . ')" text-anchor="end">Flow (CFS)</text>' : '';
    $gauge_label_x = $width - 12;
    $gauge_label = $has_gauge ? '<text x="' . $gauge_label_x . '" y="' . $mt . '" font-size="11" fill="#C04020" transform="rotate(90,' . $gauge_label_x . ',' . $mt . ')" text-anchor="end">Gage Height (Ft)</text>' : '';

    // Legend
    $legend_x = $ml + $pw - 180;
    $legend = '';
    if ($has_flow && $has_gauge) {
        $legend .= "<line x1=\"" . ($legend_x) . "\" y1=\"" . ($mt + 10) . "\" x2=\"" . ($legend_x + 20) . "\" y2=\"" . ($mt + 10) . "\" stroke=\"#2060A0\" stroke-width=\"2\"/>";
        $legend .= "<text x=\"" . ($legend_x + 24) . "\" y=\"" . ($mt + 14) . "\" font-size=\"11\" fill=\"#2060A0\">Flow</text>";
        $legend .= "<line x1=\"" . ($legend_x + 70) . "\" y1=\"" . ($mt + 10) . "\" x2=\"" . ($legend_x + 90) . "\" y2=\"" . ($mt + 10) . "\" stroke=\"#C04020\" stroke-width=\"2\"/>";
        $legend .= "<text x=\"" . ($legend_x + 94) . "\" y=\"" . ($mt + 14) . "\" font-size=\"11\" fill=\"#C04020\">Gage Height</text>";
    }

    return <<<SVG
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 $width $height" width="$width" height="$height">
<text x="{$ml}" y="18" font-size="13" font-weight="bold" fill="#333">$esc_title</text>
$flow_label
$gauge_label
$flow_grid
$gauge_grid
$x_labels
<rect x="$ml" y="$mt" width="$pw" height="$ph" fill="none" stroke="#ccc" stroke-width="0.5"/>
$flow_line
$gauge_line
$legend
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
