<?php

declare(strict_types=1);

/**
 * Consolidated reach-detail field formatters shared by /description.php
 * (description_detail.php) and /reach.php (reach_detail.php) so both pages
 * render the same condensed lines.
 *
 * Each returns the value string for a single details-table row, or null when
 * there's nothing to show (callers skip null/empty rows).
 *
 * Convention matches the includes/ directory: function-only, snake_case,
 * strict types.
 */

/**
 * Watershed line: "Wilson-Trusk-Nestuccu in Oregon, North Coast" — basin,
 * then state(s), then region, each part omitted when absent.
 *
 * @param array<string, mixed> $reach
 * @param list<string>         $states
 */
function format_reach_watershed(array $reach, array $states): ?string
{
    $basin = trim((string)($reach['basin'] ?? ''));
    $region = trim((string)($reach['region'] ?? ''));
    $state_str = implode(', ', $states);

    $out = $basin;
    if ($state_str !== '') {
        $out = $out !== '' ? "$out in $state_str" : $state_str;
    }
    if ($region !== '') {
        $out = $out !== '' ? "$out, $region" : $region;
    }
    return $out !== '' ? $out : null;
}

/**
 * Length line: "22.7 mi, gradient 11 ft/mi, max 29 ft/mi" — length anchors
 * the line; gradient + max gradient append when present.
 *
 * @param array<string, mixed> $reach
 */
function format_reach_length(array $reach): ?string
{
    $length = $reach['length'] ?? null;
    if ($length === null || (float)$length <= 0) {
        return null;
    }
    $out = number_format((float)$length, 1) . ' mi';
    if (($reach['gradient'] ?? null) !== null) {
        $out .= ', gradient ' . number_format((float)$reach['gradient'], 0) . ' ft/mi';
    }
    if (($reach['max_gradient'] ?? null) !== null) {
        $out .= ', max ' . number_format((float)$reach['max_gradient'], 0) . ' ft/mi';
    }
    return $out;
}

/**
 * Elevation line: "241 ft to 2 ft, loss 239 ft" — put-in (reach.elevation)
 * down to take-out (put-in minus loss), then the loss. Falls back to whichever
 * part is available.
 *
 * @param array<string, mixed> $reach
 */
function format_reach_elevation(array $reach): ?string
{
    $start = ($reach['elevation'] ?? null) !== null ? (float)$reach['elevation'] : null;
    $loss = ($reach['elevation_lost'] ?? null) !== null ? (float)$reach['elevation_lost'] : null;

    if ($start !== null && $loss !== null) {
        $end = $start - $loss;
        return number_format($start, 0) . ' ft to ' . number_format($end, 0)
            . ' ft, loss ' . number_format($loss, 0) . ' ft';
    }
    if ($start !== null) {
        return number_format($start, 0) . ' ft';
    }
    if ($loss !== null) {
        return 'loss ' . number_format($loss, 0) . ' ft';
    }
    return null;
}

/**
 * Flow line: "low 400 CFS, high 2,000 CFS" from the derived low/okay/high
 * bands (the "okay" band carries the low + high thresholds). The okay span
 * itself is dropped — with the current derivation it always equals
 * [low, high]; a future distinct sweet-spot range would be added here.
 *
 * @param list<array<string, mixed>> $flow_levels
 */
function format_reach_flow(array $flow_levels): ?string
{
    $okay = null;
    foreach ($flow_levels as $fl) {
        if (($fl['level'] ?? '') === 'okay') {
            $okay = $fl;
            break;
        }
    }
    if ($okay === null) {
        return null;
    }

    $parts = [];
    if (($okay['low'] ?? null) !== null) {
        $is_flow = ($okay['low_data_type'] ?? 'flow') === 'flow';
        $parts[] = 'low ' . number_format((float)$okay['low'], $is_flow ? 0 : 1)
            . ($is_flow ? ' CFS' : ' ft');
    }
    if (($okay['high'] ?? null) !== null) {
        $is_flow = ($okay['high_data_type'] ?? 'flow') === 'flow';
        $parts[] = 'high ' . number_format((float)$okay['high'], $is_flow ? 0 : 1)
            . ($is_flow ? ' CFS' : ' ft');
    }
    return $parts !== [] ? implode(', ', $parts) : null;
}
