import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';

/**
 * Boot a self-contained kayak test environment for Playwright:
 *
 *   1. Mint a tmp dir + sqlite path
 *   2. Run `levels init-db` against it (schema only — S1-cleanup; specs seed their own rows)
 *   3. Create a tiny explicit dataset-region fixture for one state landing page
 *   4. Spawn `php -S 127.0.0.1:<port> -t <tmp docroot>` against it
 *   5. Poll the port until it accepts
 *   6. Hand the {pid, tmpDir, dbPath, port} off to globalTeardown via env vars
 *
 * Mirrors tests/php/IntegrationTestCase.php's setUpBeforeClass; same
 * pattern, different runner. See docs/done/PLAN_js_smoke_tests.md Phase 1.
 *
 * No exports of `KAYAK_TEST_BASE_URL` — the Playwright config hardcodes
 * the baseURL because the config loads before this file runs (the
 * env-var hand-off would arrive too late, see playwright.config.ts).
 */
export default async function globalSetup(): Promise<void> {
  const repoRoot = path.resolve(__dirname, '..', '..');

  const levelsBin = resolveLevelsCommand(repoRoot);
  if (!levelsBin) {
    throw new Error(
      'No `levels` CLI found (looked for /home/pat/.venv/bin/levels, ' +
        `${repoRoot}/.venv/bin/levels, and PATH). JS smoke tests need it ` +
        'to seed the test DB via `levels init-db`.',
    );
  }

  const baseTmp = mkdtempSync(path.join(tmpdir(), 'kayak-js-'));
  const dbPath = path.join(baseTmp, 'kayak-test.db');
  const databaseUrl = `sqlite:///${dbPath}`;
  const configJsonPath = path.join(baseTmp, 'runtime-config.json');
  // The dataset fixture lives in its own subdir, a SIBLING of the docroot:
  // `levels build` refuses an output dir inside DATASET_DIR (S3h guard), so
  // DATASET_DIR must not be baseTmp itself (the docroot's parent).
  const datasetDir = path.join(baseTmp, 'dataset');
  mkdirSync(datasetDir);
  writeFileSync(
    path.join(datasetDir, 'region.yaml'),
    [
      'default_weather_url: https://example.com/weather',
      'states:',
      '  Oregon:',
      '    weather_url: https://example.com/weather/or',
      '    links: []',
      '',
    ].join('\n'),
    'utf8',
  );
  const baseEnv = { ...process.env };
  for (const key of Object.keys(baseEnv)) {
    if (['DATASET_DIR', 'METADATA_DIR'].includes(key.toUpperCase())) {
      delete baseEnv[key];
    }
  }

  // Env shared by `levels init-db`, `levels build`, and
  // `levels emit-config`. SQLITE_PATH lets PHP's _sqlite_path()
  // fallback chain still resolve if Config::str('database_path')
  // ever returns the empty default; DATABASE_URL drives SQLAlchemy
  // and seeds the JSON's database_path key. DATASET_DIR points at the
  // one-file region fixture above so the generic engine still emits a
  // deterministic /Oregon.html page for smoke coverage. Drop inherited
  // dataset path env vars and point HOME at the tmpdir so the operator's
  // ~/.config/kayak/.env cannot conflict.
  // EDITOR_FEATURE=1 covers two spec families with one server:
  //   * smoke.spec.ts — page-load assertions; the editor feature flag
  //     doesn't change any of the pages it visits (per-state HTML,
  //     /reach.php, /map.html, etc.) so flipping the flag on is a
  //     no-op for those tests.
  //   * editor.spec.ts (T2.5) — login → propose → review → approve
  //     flow needs /propose.php, /review.php, /edit.php live; those
  //     all return 404 under EDITOR_FEATURE=0.
  // MAIL_FROM / SITE_URL / TURNSTILE_* mirror the prod env vars so
  // the emitted JSON has the keys editor.spec.ts requires; without
  // them, Phase 4 Config strict-mode dies HTTP-500 on first read.
  const sharedEnv = {
    ...baseEnv,
    HOME: baseTmp,
    SUDO_USER: '',
    SQLITE_PATH: dbPath,
    DATABASE_URL: databaseUrl,
    DATASET_DIR: datasetDir,
    EDITOR_FEATURE: '1',
    MAIL_FROM: 'test@example.com',
    SITE_URL: 'http://127.0.0.1',
    TURNSTILE_SITE_KEY: 'TEST_SITE_KEY',
    TURNSTILE_SECRET: 'TEST_SECRET',
  };

  execFileSync(levelsBin, ['init-db'], {
    env: sharedEnv,
    stdio: 'pipe',
  });

  // Run `levels build` into a tmp dir before spawning the server.
  // Without it, every PHP page 404s on /static/levels.js and
  // /static/filters.js (sourced from src/kayak/web/static/, copied
  // by the build). Isolating to a tmp dir keeps the test from
  // clobbering the dev box's actual build output.
  const docroot = path.join(baseTmp, 'public_html');
  execFileSync(levelsBin, ['build'], {
    env: { ...sharedEnv, OUTPUT_DIR: docroot },
    stdio: 'pipe',
  });
  if (!existsSync(docroot) || !statSync(docroot).isDirectory()) {
    throw new Error(`levels build did not produce ${docroot}`);
  }

  // Phase 4 of T3.3 made PHP's Config singleton fatal-on-missing —
  // every PHP page now requires a readable runtime-config.json or
  // dies HTTP 500. Mirror tests/php/IntegrationTestCase.php and mint
  // a per-run JSON inside baseTmp so global-teardown's rmSync sweeps
  // it. KAYAK_CONFIG_PATH below points the php -S subprocess at it.
  execFileSync(levelsBin, ['emit-config', `--out=${configJsonPath}`], {
    env: sharedEnv,
    stdio: 'pipe',
  });
  if (!existsSync(configJsonPath)) {
    throw new Error(`levels emit-config did not produce ${configJsonPath}`);
  }

  const port = parseInt(process.env.KAYAK_TEST_PORT ?? '8000', 10);

  const phpProc = spawn(
    'php',
    ['-S', `127.0.0.1:${port}`, '-t', docroot],
    {
      env: {
        ...process.env,
        KAYAK_CONFIG_PATH: configJsonPath,
        // SQLITE_PATH stays as the belt-and-suspenders for PHP's
        // _sqlite_path() fallback chain (Config first, then this
        // env, then __DIR__-relative). Every other test-only setting
        // (EDITOR_FEATURE, MAIL_FROM, SITE_URL, TURNSTILE_*) is now
        // sourced from the JSON via Config::str(...) — see Phase 4.
        SQLITE_PATH: dbPath,
      },
      cwd: repoRoot,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
    },
  );

  // Buffer stderr so a fail-fast exit can include it in the error
  // message. Without this, a port-already-in-use death looks like a
  // generic "server never started" timeout, which is unactionable.
  let stderrBuf = '';
  phpProc.stderr?.on('data', (chunk: Buffer) => {
    stderrBuf += chunk.toString('utf8');
  });

  let exited = false;
  let exitInfo: { code: number | null; signal: NodeJS.Signals | null } | null = null;
  phpProc.on('exit', (code, signal) => {
    exited = true;
    exitInfo = { code, signal };
  });

  await waitForPort('127.0.0.1', port, 5_000, () => exited, () => ({
    exitInfo,
    stderr: stderrBuf,
    port,
  }));

  // Persist for globalTeardown. process.env strings only — no arbitrary
  // types — so JSON-encode the structured handoff.
  process.env.KAYAK_TEST_PID = String(phpProc.pid);
  process.env.KAYAK_TEST_TMPDIR = baseTmp;
  process.env.KAYAK_TEST_DBPATH = dbPath;
  process.env.KAYAK_TEST_PORT = String(port);
}

/** Locate the `levels` CLI. Prefers the prod venv, then the repo's .venv, then PATH. */
function resolveLevelsCommand(repoRoot: string): string | null {
  const candidates = [
    '/home/pat/.venv/bin/levels',
    path.join(repoRoot, '.venv', 'bin', 'levels'),
  ];
  for (const c of candidates) {
    try {
      if (statSync(c).isFile()) return c;
    } catch {
      // ignore — fall through
    }
  }
  // PATH lookup via execFileSync ('which' on macOS/Linux, 'where' on Windows;
  // tests assume POSIX since the PHP layer is POSIX-only).
  try {
    const out = execFileSync('which', ['levels'], { encoding: 'utf8' }).trim();
    return out !== '' ? out : null;
  } catch {
    return null;
  }
}

/**
 * Poll <host>:<port> until a TCP connect succeeds, or throw after
 * <timeoutMs>. If <exitedCb> returns true mid-wait (the server died),
 * fail fast with the exit + stderr context from <contextCb> instead
 * of waiting out the full timeout — port-in-use / PHP-boot-fatal
 * deaths surface in <100 ms but the timeout error alone is
 * unactionable.
 */
async function waitForPort(
  host: string,
  port: number,
  timeoutMs: number,
  exitedCb: () => boolean,
  contextCb: () => { exitInfo: { code: number | null; signal: NodeJS.Signals | null } | null; stderr: string; port: number },
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  // Initial settle: PHP -S death on bind-failure is ~50 ms; wait a
  // tick so the 'exit' event lands before the squatter race below.
  await sleep(100);
  while (Date.now() < deadline) {
    if (exitedCb()) {
      throwDeadServer(host, contextCb());
    }
    const ok = await tryConnect(host, port);
    if (ok) {
      // The port responded — but did OUR process bind it, or is a
      // squatter on the same port serving the bytes? If our spawned
      // PHP exited after the connect, it died on bind ("Address
      // already in use") and the connect we just saw went to whoever
      // had the port first.
      await sleep(50);
      if (exitedCb()) {
        throwDeadServer(host, contextCb());
      }
      return;
    }
    await sleep(50);
  }
  throw new Error(
    `PHP server at ${host}:${port} never started accepting within ${timeoutMs}ms ` +
      `(process still running, port not bound)`,
  );
}

function throwDeadServer(
  host: string,
  ctx: { exitInfo: { code: number | null; signal: NodeJS.Signals | null } | null; stderr: string; port: number },
): never {
  const exitDesc = ctx.exitInfo
    ? `exited (code=${ctx.exitInfo.code} signal=${ctx.exitInfo.signal})`
    : 'exited';
  throw new Error(
    `PHP server died before/while listening on ${host}:${ctx.port}: ${exitDesc}\n` +
      `stderr:\n${ctx.stderr || '(empty)'}\n` +
      'Common cause: another process is already bound to that port. ' +
      'Override with KAYAK_TEST_PORT=<other>.',
  );
}

function tryConnect(host: string, port: number): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    const sock = net.createConnection({ host, port }, () => {
      sock.end();
      resolve(true);
    });
    sock.on('error', () => resolve(false));
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
