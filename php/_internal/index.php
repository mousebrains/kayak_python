<?php
declare(strict_types=1);
/**
 * /_internal/ — maintainer-only operator dashboard (Phase 2.4).
 *
 * Audience: the site maintainer answering "is anything weird in the
 * data right now?" without SSHing in. Auth reuses the editor_session
 * cookie (no new credential); only editors with status='maintainer'
 * pass require_maintainer(). nginx adds X-Robots-Tag noindex and only
 * mounts this URL on the canonical levels.wkcc.org vhost — the
 * mousebrains vhost returns 404 for /_internal/.
 *
 * Per docs/done/PLAN_internal_dashboard.md (iter 5, 2026-05-15).
 */

require_once __DIR__ . '/../includes/db.php';
require_once __DIR__ . '/../includes/auth.php';
require_once __DIR__ . '/../includes/csp_classify.php';

$editor = require_maintainer();
$db     = get_db();

const STALE_THRESHOLD_HOURS  = 48;
const EXPIRED_THRESHOLD_DAYS = 7;
const CSP_RECENT_LIMIT       = 50;
const CSP_RECENT_WINDOW_DAYS = 7;

/**
 * Narrow PDO::query()'s PDOStatement|false return for PHPStan.
 * ERRMODE_EXCEPTION (db.php) means the false branch is unreachable
 * at runtime, but the static type still includes it. Same pattern as
 * status.php's status_query().
 */
function dash_query(PDO $db, string $sql): PDOStatement {
    $stmt = $db->query($sql);
    if ($stmt === false) {
        throw new RuntimeException('PDO query returned false: ' . $sql);
    }
    return $stmt;
}

/** Format an ISO-ish timestamp into "5m ago" / "2h ago" / etc. */
function age_phrase(?string $ts): string {
    if ($ts === null || $ts === '') {
        return '—';
    }
    $secs = time() - (int)strtotime($ts . ' UTC');
    if ($secs < 0) {
        return 'in the future?';
    }
    if ($secs < 60)         { return $secs . 's ago'; }
    if ($secs < 3600)       { return (int)($secs / 60) . 'm ago'; }
    if ($secs < 86400)      { return (int)($secs / 3600) . 'h ago'; }
    return (int)($secs / 86400) . 'd ago';
}

// csp_classify() lives in php/includes/csp_classify.php (required above) so it
// can be unit-tested without booting this maintainer-only page.

/** Bucket a source's freshness against the 48h / 7d thresholds. */
function age_bucket(?string $ts): string {
    if ($ts === null || $ts === '') {
        return 'none';
    }
    $age_secs = time() - (int)strtotime($ts . ' UTC');
    if ($age_secs < STALE_THRESHOLD_HOURS * 3600)        { return 'fresh'; }
    if ($age_secs < EXPIRED_THRESHOLD_DAYS * 86400)      { return 'stale'; }
    return 'expired';
}

/** filesize() returns false on missing/dir; coerce to null. */
function safe_filesize(string $path): ?int {
    if (!file_exists($path)) {
        return null;
    }
    $n = @filesize($path);
    return $n === false ? null : $n;
}

/** Human-readable bytes (1024-based). */
function human_bytes(?int $bytes): string {
    if ($bytes === null) {
        return '—';
    }
    $units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    $i = 0;
    $n = (float)$bytes;
    while ($n >= 1024 && $i < count($units) - 1) {
        $n /= 1024;
        $i++;
    }
    return sprintf('%.1f %s', $n, $units[$i]);
}

/**
 * Tail-read up to ~N lines from a file. Reads the file from EOF
 * backwards in 16 KiB chunks until N newlines are seen or we hit BOF.
 * Returns the lines newest-first.
 *
 * @return list<string>
 */
function tail_lines(string $path, int $max_lines): array {
    if (!file_exists($path)) {
        return [];
    }
    $fh = @fopen($path, 'rb');
    if ($fh === false) {
        return [];
    }
    $chunk_size = 16384;
    fseek($fh, 0, SEEK_END);
    $pos = ftell($fh);
    $buf = '';
    $lines = 0;
    while ($pos > 0 && $lines <= $max_lines) {
        $read = min($chunk_size, $pos);
        $pos -= $read;
        fseek($fh, $pos, SEEK_SET);
        $chunk = (string)fread($fh, $read);
        $buf = $chunk . $buf;
        $lines = substr_count($buf, "\n");
    }
    fclose($fh);
    $split = preg_split('/\R/', $buf);
    $arr = $split !== false ? $split : [];
    $out = array_values(array_filter($arr, static fn($s) => $s !== ''));
    return array_slice(array_reverse($out), 0, $max_lines);
}

// ---------------------------------------------------------------------------
// Section 1: Build + data freshness
// ---------------------------------------------------------------------------
$index_path  = __DIR__ . '/../../public_html/index.html';
$build_mtime = file_exists($index_path) ? filemtime($index_path) : false;
$build_at    = $build_mtime !== false ? gmdate('Y-m-d H:i:s', $build_mtime) . ' UTC' : null;

$row = dash_query($db, 'SELECT MAX(observed_at) AS m FROM latest_observation')->fetch();
$latest_obs_at = (is_array($row) && $row['m'] !== null) ? (string)$row['m'] : null;

$db_path = _sqlite_path();
$db_size  = safe_filesize($db_path);
$wal_size = safe_filesize($db_path . '-wal');
$shm_size = safe_filesize($db_path . '-shm');

$schema_head = dash_query($db,
    'SELECT version FROM schema_migrations ORDER BY applied_at DESC LIMIT 1'
)->fetchColumn();
$schema_head = is_string($schema_head) ? $schema_head : '—';

// ---------------------------------------------------------------------------
// Section 2: Aggregate counts
// ---------------------------------------------------------------------------
$counts = [
    'sources'         => (int)dash_query($db, 'SELECT COUNT(*) FROM source')->fetchColumn(),
    'gauges'          => (int)dash_query($db, 'SELECT COUNT(*) FROM gauge')->fetchColumn(),
    'reaches'         => (int)dash_query($db, 'SELECT COUNT(*) FROM reach')->fetchColumn(),
    'observations'    => (int)dash_query($db, 'SELECT COUNT(*) FROM observation')->fetchColumn(),
    'active_sources'  => (int)dash_query($db,
        'SELECT COUNT(DISTINCT source_id) FROM latest_observation'
    )->fetchColumn(),
];

// ---------------------------------------------------------------------------
// Section 3: Per-source freshness
// ---------------------------------------------------------------------------
// All 299 production sources link to exactly one gauge via gauge_source,
// so a simple LEFT JOIN g (via gs) does the right thing here — no
// GROUP_CONCAT or DISTINCT needed.
$source_rows = dash_query($db,
    'SELECT s.id, s.name, s.agency,
            MAX(lo.observed_at) AS latest_at,
            g.river   AS river,
            g.location AS location
     FROM source s
     LEFT JOIN latest_observation lo ON lo.source_id = s.id
     LEFT JOIN gauge_source gs       ON gs.source_id = s.id
     LEFT JOIN gauge g               ON g.id         = gs.gauge_id
     GROUP BY s.id, s.name, s.agency, g.river, g.location
     ORDER BY (latest_at IS NULL), latest_at ASC'
)->fetchAll();

// ---------------------------------------------------------------------------
// Section 4: Recent CSP violations
// ---------------------------------------------------------------------------
$csp_log_path = Config::str('csp_log_path', '/home/pat/logs/csp.log');
$csp_lines    = tail_lines($csp_log_path, 200);
// Also fold in csp.log.1 if it's rotated within the window (skip .gz).
$rotated_path = $csp_log_path . '.1';
if (file_exists($rotated_path)) {
    $mtime = filemtime($rotated_path);
    if ($mtime !== false && (time() - $mtime) < CSP_RECENT_WINDOW_DAYS * 86400) {
        $csp_lines = array_merge($csp_lines, tail_lines($rotated_path, 200));
    }
}
$csp_recent  = [];
$window_secs = CSP_RECENT_WINDOW_DAYS * 86400;
$now_ts      = time();
foreach ($csp_lines as $line) {
    $decoded = json_decode($line, true);
    if (!is_array($decoded)) {
        continue;
    }
    // Records carry a top-level "ts" written by csp-report.php at receive
    // time; fall back to the embedded reported field if missing.
    $ts_str = $decoded['ts'] ?? $decoded['_received_at'] ?? null;
    if (!is_string($ts_str)) {
        continue;
    }
    $ts = (int)strtotime($ts_str);
    if ($ts === 0 || ($now_ts - $ts) > $window_secs) {
        continue;
    }
    $csp_recent[] = ['ts' => $ts_str, 'data' => $decoded];
    if (count($csp_recent) >= CSP_RECENT_LIMIT) {
        break;
    }
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
header('Content-Type: text/html; charset=utf-8');
header('Cache-Control: no-store, private');
header('X-Robots-Tag: noindex, nofollow');

$signed_in_email = htmlspecialchars((string)($editor['email'] ?? ''));
$signed_in_seen  = htmlspecialchars((string)($editor['session_last_seen_at'] ?? '—'));

?><!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Internal dashboard — kayak</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<style>
:root {
    --fresh:   #d6f5d6;
    --stale:   #fff3c4;
    --expired: #ffd9a8;
    --none:    #f5c5c5;
    --rule:    #d8d8d8;
    --muted:   #666;
}
body { font: 14px/1.4 system-ui, sans-serif; max-width: 1100px; margin: 1.5rem auto; padding: 0 1rem; color: #222; }
h1 { font-size: 1.4rem; margin: 0 0 .25rem; }
h2 { font-size: 1.05rem; margin: 1.5rem 0 .4rem; padding-bottom: .15rem; border-bottom: 1px solid var(--rule); }
.header-meta { color: var(--muted); margin: 0 0 1rem; }
.header-meta a { margin-left: .75rem; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: .25rem .5rem; border-bottom: 1px solid var(--rule); text-align: left; vertical-align: top; }
th { background: #fafafa; font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr[data-age-bucket="fresh"]   td.age-cell { background: var(--fresh); }
tr[data-age-bucket="stale"]   td.age-cell { background: var(--stale); }
tr[data-age-bucket="expired"] td.age-cell { background: var(--expired); }
tr[data-age-bucket="none"]    td.age-cell { background: var(--none); }
.summary-grid { display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1.5rem; max-width: 600px; }
.summary-grid dt { color: var(--muted); }
.summary-grid dd { margin: 0; font-variant-numeric: tabular-nums; }
details.csp { margin-top: .5rem; }
details.csp summary { cursor: pointer; color: var(--muted); }
details.collapsible { margin: .5rem 0 1.25rem; }
details.collapsible > summary { cursor: pointer; color: #0066cc; font-weight: 600;
    padding: .35rem 0; user-select: none; font-size: 1.05rem; }
details.collapsible > summary:hover { text-decoration: underline; }
details.collapsible[open] > summary { margin-bottom: .4rem; }
details.collapsible > summary .meta { color: var(--muted); font-weight: normal;
    font-size: .9rem; margin-left: .5rem; }
details.collapsible table th { cursor: pointer; }
details.collapsible table th .sort-indicator { color: var(--muted); margin-left: .25rem; }
pre { font-size: 12px; background: #f5f5f5; padding: .5rem; overflow-x: auto; margin: .25rem 0; }
.quick-links a { display: inline-block; margin-right: 1rem; }
</style>
</head>
<body>

<h1>Internal dashboard</h1>
<p class="header-meta">
    Signed in as <strong><?= $signed_in_email ?></strong>
    (maintainer, last seen <?= $signed_in_seen ?> UTC).
    <a href="/logout.php">Logout</a>
</p>

<h2>Build + data freshness</h2>
<dl class="summary-grid">
    <dt>Last build</dt>
    <dd><?= htmlspecialchars($build_at ?? '—') ?>
        (<?= htmlspecialchars(age_phrase($build_mtime !== false ? gmdate('Y-m-d H:i:s', $build_mtime) : null)) ?>)</dd>
    <dt>Latest observation</dt>
    <dd><?= htmlspecialchars($latest_obs_at ?? '—') ?>
        (<?= htmlspecialchars(age_phrase($latest_obs_at)) ?>)</dd>
    <dt>DB size</dt>
    <dd><?= htmlspecialchars(human_bytes($db_size)) ?>
        + WAL <?= htmlspecialchars(human_bytes($wal_size)) ?>
        + SHM <?= htmlspecialchars(human_bytes($shm_size)) ?></dd>
    <dt>Schema head</dt>
    <dd><?= htmlspecialchars($schema_head) ?></dd>
</dl>

<h2>Aggregate counts</h2>
<dl class="summary-grid">
    <dt>Sources</dt>
    <dd><?= number_format($counts['sources']) ?>
        (<?= number_format($counts['active_sources']) ?> with observations)</dd>
    <dt>Gauges</dt>
    <dd><?= number_format($counts['gauges']) ?></dd>
    <dt>Reaches</dt>
    <dd><?= number_format($counts['reaches']) ?></dd>
    <dt>Observations</dt>
    <dd><?= number_format($counts['observations']) ?></dd>
</dl>

<h2>Per-source freshness</h2>
<details class="collapsible">
    <summary>
        <?= count($source_rows) ?> sources
        <span class="meta">thresholds: fresh &lt; <?= STALE_THRESHOLD_HOURS ?>h, stale &lt; <?= EXPIRED_THRESHOLD_DAYS ?>d — click column headings to sort</span>
    </summary>
<table>
    <thead>
        <tr><th>ID</th><th>Source</th><th>Agency</th><th>River</th><th>Location</th><th>Latest observation</th><th>Age</th></tr>
    </thead>
    <tbody>
<?php foreach ($source_rows as $r): ?>
    <?php $bucket = age_bucket(is_string($r['latest_at']) ? $r['latest_at'] : null); ?>
        <tr data-age-bucket="<?= $bucket ?>">
            <td class="num"><a href="/source.php?id=<?= (int)$r['id'] ?>"><?= (int)$r['id'] ?></a></td>
            <td><?= htmlspecialchars((string)$r['name']) ?></td>
            <td><?= htmlspecialchars($r['agency'] === null ? '—' : (string)$r['agency']) ?></td>
            <td><?= htmlspecialchars(is_string($r['river']) && $r['river'] !== '' ? $r['river'] : '—') ?></td>
            <td><?= htmlspecialchars(is_string($r['location']) && $r['location'] !== '' ? $r['location'] : '—') ?></td>
            <td><?= htmlspecialchars(is_string($r['latest_at']) ? $r['latest_at'] : '—') ?></td>
            <td class="age-cell"><?= htmlspecialchars(age_phrase(is_string($r['latest_at']) ? $r['latest_at'] : null)) ?></td>
        </tr>
<?php endforeach ?>
    </tbody>
</table>
</details>

<h2>Recent CSP violations</h2>
<?php if (count($csp_recent) === 0): ?>
    <p style="color: var(--muted)">No CSP violations in the window. Log path:
        <code><?= htmlspecialchars($csp_log_path) ?></code></p>
<?php else: ?>
<details class="collapsible">
    <summary>
        <?= count($csp_recent) ?> shown
        <span class="meta">last <?= CSP_RECENT_LIMIT ?> in <?= CSP_RECENT_WINDOW_DAYS ?> days — click column headings to sort</span>
    </summary>
    <table>
        <thead>
            <tr><th>Time</th><th>Likely source</th><th>Document</th><th>Violated</th><th>Blocked</th></tr>
        </thead>
        <tbody>
<?php foreach ($csp_recent as $e):
    $data       = $e['data'];
    // csp-report.php normalizes to keys `document_uri` / `violated` / `blocked`;
    // keep the raw Reporting-API names as fallbacks.
    $doc        = (string)($data['document_uri'] ?? $data['documentURL'] ?? '—');
    $violated   = (string)($data['violated'] ?? $data['violated_directive'] ?? $data['effectiveDirective'] ?? '—');
    $blocked    = (string)($data['blocked'] ?? $data['blocked_uri'] ?? $data['blockedURL'] ?? '—');
    $cause      = csp_classify($data);
?>
            <tr>
                <td><?= htmlspecialchars($e['ts']) ?></td>
                <td><?= htmlspecialchars($cause) ?></td>
                <td><?= htmlspecialchars($doc) ?></td>
                <td><?= htmlspecialchars($violated) ?></td>
                <td><?= htmlspecialchars($blocked) ?></td>
            </tr>
<?php endforeach ?>
        </tbody>
    </table>
</details>
<?php endif ?>

<h2>Quick links</h2>
<p class="quick-links">
    <a href="/_internal/status">Operator status (24h)</a>
    <a href="/status.json">/status.json</a>
    <a href="/gauges.html">/gauges.html</a>
    <a href="/map.html">/map.html</a>
    <a href="https://status.mousebrains.com" rel="noopener">Status page</a>
    <a href="https://uptime.betterstack.com" rel="noopener">Better Stack</a>
</p>

<?php
// Cache-bust the JS against the nginx `Cache-Control: …immutable` policy on
// /static/. Without the ?v= the browser pins last download for a year and
// silently keeps a stale selector after we ship a fix. mtime is monotonic
// and changes every deploy so it does the job without a hash pipeline.
$js_path  = __DIR__ . '/../../public_html/static/internal-sort.js';
$js_mtime = file_exists($js_path) ? (int)filemtime($js_path) : 0;
?>
<script src="/static/internal-sort.js?v=<?= $js_mtime ?>" defer></script>
</body>
</html>
