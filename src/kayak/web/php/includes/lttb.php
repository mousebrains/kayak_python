<?php
declare(strict_types=1);
/**
 * Largest Triangle Three Buckets (LTTB) downsampling.
 *
 * @param  list<array{0: int|float, 1: int|float}> $data       [x, y] pairs, sorted by x.
 * @param  int                                     $threshold  Target number of output points.
 * @return list<array{0: int|float, 1: int|float}>             Downsampled [x, y] pairs.
 */
function lttb_downsample(array $data, int $threshold): array {
    $n = count($data);
    if ($threshold >= $n || $threshold < 3) return $data;

    $sampled = [$data[0]];
    $bucket_size = ($n - 2) / ($threshold - 2);
    [$a_x, $a_y] = $data[0];

    for ($i = 0; $i < $threshold - 2; $i++) {
        // Average of next bucket
        $avg_start = (int)(($i + 1) * $bucket_size) + 1;
        $avg_end   = min((int)(($i + 2) * $bucket_size) + 1, $n - 1);
        $count = max($avg_end - $avg_start, 1);
        $avg_x = $avg_y = 0.0;
        for ($j = $avg_start; $j < $avg_end; $j++) {
            $avg_x += $data[$j][0];
            $avg_y += $data[$j][1];
        }
        $avg_x /= $count;
        $avg_y /= $count;

        // Current bucket — pick largest triangle
        $range_start = (int)($i * $bucket_size) + 1;
        $range_end   = min((int)(($i + 1) * $bucket_size) + 1, $n - 1);
        $max_area = -1.0;
        $max_idx  = $range_start;
        for ($j = $range_start; $j < $range_end; $j++) {
            $area = abs(
                ($a_x - $avg_x) * ($data[$j][1] - $a_y)
                - ($a_x - $data[$j][0]) * ($avg_y - $a_y)
            );
            if ($area > $max_area) {
                $max_area = $area;
                $max_idx  = $j;
            }
        }
        $sampled[] = $data[$max_idx];
        [$a_x, $a_y] = $data[$max_idx];
    }
    $sampled[] = $data[$n - 1];
    return $sampled;
}
