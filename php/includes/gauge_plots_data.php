<?php

declare(strict_types=1);

/**
 * Gauge-keyed DB lookups used by gauge_plots.php — `is recent data
 * available?` predicates plus a single fetch that runs the despike+
 * cross-source-mean pipeline when the gauge has 2+ sources.
 *
 * Helpers keep their pre-extract `_gp_` prefix (file-private to the
 * gauge_plots cluster); the prefix doesn't clash with anything
 * outside the cluster (see Tier 5 CI-lesson note in
 * docs/done/PLAN_php_layer_split.md). Split out as part of Tier 5.GP so
 * the DB surface is testable / readable on its own.
 */

require_once __DIR__ . '/gauge_plots_filter.php';

/** True iff the gauge's latest observation of $type is within the last $hours. */
function _gp_has_current_obs(PDO $db, int $gauge_id, string $type, int $hours): bool
{
    $stmt = $db->prepare(
        'SELECT MAX(o.observed_at) FROM observation o
         JOIN gauge_source gs ON o.source_id = gs.source_id
         WHERE gs.gauge_id = ? AND o.data_type = ?'
    );
    $stmt->execute([$gauge_id, $type]);
    $latest = $stmt->fetchColumn();
    if (!$latest) {
        return false;
    }
    $ts = strtotime((string)$latest . ' UTC');
    if ($ts === false) {
        return false;
    }
    return $ts >= (time() - $hours * 3600);
}

/** True iff at least one observation of $type exists for the gauge in [since, until]. */
function _gp_has_obs(PDO $db, int $gauge_id, string $type, string $since, ?string $until): bool
{
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

/**
 * Fetch [times[], values[]] for one data_type in the visible window.
 *
 * When the gauge has 2+ sources contributing to this data_type (USGS+NWS
 * fanout from the source split), each source's stream is despiked
 * (Hampel filter, 30-min window) and then averaged across sources
 * (per-source mean, then equal-weight mean across sources) to suppress
 * quarter-hour zigzag from disagreeing rating curves while keeping the
 * line at the midpoint of the two feeds. Single-source data is returned
 * verbatim.
 *
 * @return array{0: list<int>, 1: list<float>}
 */
function _gp_fetch_series(PDO $db, int $gauge_id, string $type, string $since, ?string $until): array
{
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
    $times = [];
    $values = [];
    $sources = [];
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
