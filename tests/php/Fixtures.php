<?php

declare(strict_types=1);

/**
 * Test-data factories over the real (init-db) schema.
 *
 * Each method inserts one row with sensible NOT-NULL defaults and merges an
 * `$overrides` map (override keys win). Methods that hit an autoincrement table
 * return the new id; junction / composite-PK inserts return void. Keeps
 * FunctionalTestCase::seedDatabase and integration seeders concise and in
 * lockstep with models.py (the schema comes from `levels init-db`, not a
 * hand-written fixture).
 */
final class Fixtures
{
    private static int $seq = 0;

    /** @param array<string, mixed> $row @return int lastInsertId */
    private static function insert(PDO $db, string $table, array $row): int
    {
        $cols = array_keys($row);
        $sql = sprintf(
            'INSERT INTO %s (%s) VALUES (%s)',
            $table,
            implode(', ', $cols),
            implode(', ', array_fill(0, count($cols), '?')),
        );
        $db->prepare($sql)->execute(array_values($row));
        return (int) $db->lastInsertId();
    }

    /** @param array<string, mixed> $overrides */
    public static function gauge(PDO $db, array $overrides = []): int
    {
        $n = ++self::$seq;
        return self::insert($db, 'gauge', $overrides + ['name' => "Gauge $n"]);
    }

    /** @param array<string, mixed> $overrides */
    public static function source(PDO $db, array $overrides = []): int
    {
        $n = ++self::$seq;
        return self::insert($db, 'source', $overrides + ['name' => "Source $n"]);
    }

    public static function linkGaugeSource(PDO $db, int $gaugeId, int $sourceId): void
    {
        self::insert($db, 'gauge_source', ['gauge_id' => $gaugeId, 'source_id' => $sourceId]);
    }

    /** @param array<string, mixed> $overrides */
    public static function reach(PDO $db, array $overrides = []): int
    {
        $n = ++self::$seq;
        return self::insert($db, 'reach', $overrides + [
            'name' => "Reach $n",
            'sort_name' => "reach $n",
        ]);
    }

    /** Latest cached observation for a gauge + data_type. @param array<string, mixed> $overrides */
    public static function latestGaugeObservation(PDO $db, int $gaugeId, array $overrides = []): void
    {
        self::insert($db, 'latest_gauge_observation', $overrides + [
            'gauge_id' => $gaugeId,
            'data_type' => 'flow',
            'observed_at' => date('Y-m-d H:i:s'),
            'value' => 100.0,
        ]);
    }

    /** A raw time-series observation (composite PK, no id). @param array<string, mixed> $overrides */
    public static function observation(PDO $db, int $sourceId, array $overrides = []): void
    {
        self::insert($db, 'observation', $overrides + [
            'source_id' => $sourceId,
            'data_type' => 'flow',
            'observed_at' => date('Y-m-d H:i:s'),
            'value' => 100.0,
        ]);
    }

    /** @param array<string, mixed> $overrides */
    public static function reachClass(PDO $db, int $reachId, array $overrides = []): int
    {
        return self::insert($db, 'reach_class', $overrides + ['reach_id' => $reachId, 'name' => 'III']);
    }

    /**
     * A `state` reference row. Returns the new id so callers can link reaches.
     *
     * @param array<string, mixed> $overrides
     */
    public static function state(PDO $db, array $overrides = []): int
    {
        $n = ++self::$seq;
        return self::insert($db, 'state', $overrides + [
            'name' => "State $n",
            'abbreviation' => 'X' . $n,
        ]);
    }

    public static function linkReachState(PDO $db, int $reachId, int $stateId): void
    {
        self::insert($db, 'reach_state', ['reach_id' => $reachId, 'state_id' => $stateId]);
    }

    /** A WBD HUC name lookup row (HUC2/4/6/8/10/12). @param array<string, mixed> $overrides */
    public static function hucName(PDO $db, string $code, int $level, string $name): void
    {
        self::insert($db, 'huc_name', ['code' => $code, 'level' => $level, 'name' => $name]);
    }

    /** A guidebook reference row. @param array<string, mixed> $overrides */
    public static function guidebook(PDO $db, array $overrides = []): int
    {
        $n = ++self::$seq;
        return self::insert($db, 'guidebook', $overrides + ['title' => "Guidebook $n"]);
    }

    public static function linkReachGuidebook(PDO $db, int $reachId, int $guidebookId): void
    {
        self::insert($db, 'reach_guidebook', ['reach_id' => $reachId, 'guidebook_id' => $guidebookId]);
    }

    /** Latest cached observation for a source + data_type. @param array<string, mixed> $overrides */
    public static function latestObservation(PDO $db, int $sourceId, array $overrides = []): void
    {
        self::insert($db, 'latest_observation', $overrides + [
            'source_id' => $sourceId,
            'data_type' => 'flow',
            'observed_at' => date('Y-m-d H:i:s'),
            'value' => 100.0,
        ]);
    }

    /**
     * Generic insert that returns lastInsertId — for one-off reference rows a
     * test needs without a dedicated factory. @param array<string, mixed> $row
     */
    public static function insertReturning(PDO $db, string $table, array $row): int
    {
        return self::insert($db, $table, $row);
    }

    /** @param array<string, mixed> $overrides */
    public static function editor(PDO $db, array $overrides = []): int
    {
        $n = ++self::$seq;
        return self::insert($db, 'editor', $overrides + [
            'email' => "editor$n@example.com",
            'status' => 'full',
        ]);
    }

    /** @param array<string, mixed> $overrides */
    public static function changeRequest(PDO $db, int $editorId, array $overrides = []): int
    {
        return self::insert($db, 'change_request', $overrides + [
            'editor_id' => $editorId,
            'target_type' => 'reach',
            'submitted_at' => date('Y-m-d H:i:s'),
            'payload_json' => '{}',
            'status' => 'pending',
        ]);
    }
}
