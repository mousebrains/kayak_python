<?php
require_once __DIR__ . '/lttb.php';

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
 * @return string  SVG markup.
 */
function generate_svg_plot(
    array $times,
    array $values,
    string $title,
    string $y_label,
    int $width = 600,
    int $height = 250,
    int $target_points = 200
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
    $ml = 60; $mr = 15; $mt = 30; $mb = 40;
    $pw = $width - $ml - $mr;   // plot width
    $ph = $height - $mt - $mb;  // plot height

    // Ranges
    $x_min = $pairs[0][0];
    $x_max = $pairs[count($pairs) - 1][0];
    $y_vals = array_column($pairs, 1);
    $y_min = min($y_vals);
    $y_max = max($y_vals);
    $y_pad = ($y_max - $y_min) * 0.05;
    if ($y_pad < 0.01) $y_pad = 1.0;
    $y_min -= $y_pad;
    $y_max += $y_pad;
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

    // Grid lines and labels
    $grid = '';
    $n_yticks = 5;
    for ($i = 0; $i <= $n_yticks; $i++) {
        $yv = $y_min + ($y_range * $i / $n_yticks);
        $py = $mt + (int)(($y_max - $yv) / $y_range * $ph);
        $label = number_format($yv, $yv == (int)$yv ? 0 : 1);
        $grid .= "<line x1=\"$ml\" y1=\"$py\" x2=\"" . ($ml + $pw) . "\" y2=\"$py\" stroke=\"#ddd\" stroke-width=\"0.5\"/>\n";
        $grid .= "<text x=\"" . ($ml - 5) . "\" y=\"" . ($py + 3) . "\" text-anchor=\"end\" font-size=\"10\" fill=\"#666\">$label</text>\n";
    }

    // X-axis date labels
    $span_days = ($x_max - $x_min) / 86400;
    $n_xticks = $span_days > 14 ? 5 : ($span_days > 3 ? (int)$span_days : 6);
    $n_xticks = max($n_xticks, 2);
    for ($i = 0; $i <= $n_xticks; $i++) {
        $xv = $x_min + ($x_range * $i / $n_xticks);
        $px = $ml + (int)(($xv - $x_min) / $x_range * $pw);
        $label = $span_days > 3 ? date('n/j', (int)$xv) : date('n/j H:i', (int)$xv);
        $grid .= "<text x=\"$px\" y=\"" . ($height - 8) . "\" text-anchor=\"middle\" font-size=\"10\" fill=\"#666\">$label</text>\n";
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
