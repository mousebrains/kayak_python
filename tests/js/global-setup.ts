import { execFileSync, spawn } from 'node:child_process';
import { existsSync, mkdtempSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import net from 'node:net';

/**
 * Boot a self-contained kayak test environment for Playwright:
 *
 *   1. Mint a tmp dir + sqlite path
 *   2. Run `levels init-db` against it (seeds schema + states + sources)
 *   3. Spawn `php -S 127.0.0.1:<port> -t public_html` against it
 *   4. Poll the port until it accepts
 *   5. Hand the {pid, tmpDir, dbPath, port} off to globalTeardown via env vars
 *
 * Mirrors tests/php/IntegrationTestCase.php's setUpBeforeClass; same
 * pattern, different runner. See docs/PLAN_js_smoke_tests.md Phase 1.
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

  // Both env vars are required — kayak.config reads DATABASE_URL for
  // SQLAlchemy; PHP reads SQLITE_PATH from the process env (mirroring
  // nginx's fastcgi_param). One without the other → partial seed or
  // PHP-side `Cannot open database`.
  const sharedEnv = {
    ...process.env,
    SQLITE_PATH: dbPath,
    DATABASE_URL: databaseUrl,
    EDITOR_FEATURE: '0',
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

  const port = parseInt(process.env.KAYAK_TEST_PORT ?? '8000', 10);

  const phpProc = spawn(
    'php',
    ['-S', `127.0.0.1:${port}`, '-t', docroot],
    {
      env: {
        ...process.env,
        SQLITE_PATH: dbPath,
        DATABASE_URL: databaseUrl,
        EDITOR_FEATURE: '0',
        MAIL_FROM: 'test@example.com',
        SITE_URL: 'http://127.0.0.1',
        TURNSTILE_SITE_KEY: 'TEST_SITE_KEY',
        TURNSTILE_SECRET: 'TEST_SECRET',
      },
      cwd: repoRoot,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
    },
  );

  // Surface server stderr if the process dies before serving requests —
  // helps diagnose "port in use" or PHP fatal-on-boot failures.
  phpProc.stderr?.on('data', () => { /* drained but not echoed; on failure the report HTML keeps the artifact */ });

  await waitForPort('127.0.0.1', port, 5_000);

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

/** Poll <host>:<port> until a TCP connect succeeds, or throw after <timeoutMs>. */
async function waitForPort(host: string, port: number, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ok = await tryConnect(host, port);
    if (ok) return;
    await sleep(50);
  }
  throw new Error(`PHP server at ${host}:${port} never started accepting within ${timeoutMs}ms`);
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
