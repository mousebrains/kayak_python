<?php

declare(strict_types=1);

/**
 * CLI helper: print the resolved Config snapshot to stdout.
 *
 * Mirrors `levels show-config --format table` on the Python side, but
 * exercises PHP's actual Config::get_singleton() load path — useful
 * during incident response when you want to confirm what PHP is
 * reading (not just what's on disk via `sudo cat /etc/kayak/
 * runtime-config.json`).
 *
 * Run from the repo root:
 *     php /home/pat/kayak/src/kayak/web/php/show-config.php
 *
 * Override the source path for tests / dry runs:
 *     KAYAK_CONFIG_PATH=/tmp/runtime-config.json \
 *         php /home/pat/kayak/src/kayak/web/php/show-config.php
 *
 * Refuses to serve via HTTP — the script has no auth and the JSON
 * carries plaintext secrets (mode 0640 root:www-data).
 */

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit(1);
}

require_once __DIR__ . '/includes/config.php';
Config::dump();
