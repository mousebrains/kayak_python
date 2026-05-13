import { rmSync } from 'node:fs';

/**
 * Mirror of tests/php/IntegrationTestCase.php::tearDownAfterClass.
 *
 * Cleans up everything globalSetup spun up:
 *   - SIGTERM the php -S process (SIGKILL fallback after 500ms)
 *   - rm -rf the tmp dir (sqlite DB + WAL files live here)
 *
 * Crashed runs (where globalSetup itself threw) skip teardown — the
 * tmpdir and any orphaned PHP server are deliberately left behind so
 * the operator can inspect.
 */
export default async function globalTeardown(): Promise<void> {
  const pidStr = process.env.KAYAK_TEST_PID;
  const tmpDir = process.env.KAYAK_TEST_TMPDIR;

  if (pidStr) {
    const pid = parseInt(pidStr, 10);
    if (!Number.isNaN(pid)) {
      try {
        process.kill(pid, 'SIGTERM');
        await sleep(500);
        // If it's still running, escalate.
        try {
          process.kill(pid, 0);
          process.kill(pid, 'SIGKILL');
        } catch {
          // 0-signal threw → process is already gone, which is what we want.
        }
      } catch {
        // SIGTERM target already gone — fine.
      }
    }
  }

  if (tmpDir) {
    try {
      rmSync(tmpDir, { recursive: true, force: true });
    } catch {
      // Best-effort cleanup; CI tmpfs gets wiped between runs anyway.
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
