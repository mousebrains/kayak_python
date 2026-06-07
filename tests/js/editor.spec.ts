import { test, expect, type BrowserContext } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomBytes, createHash } from 'node:crypto';

/**
 * Editor-journey E2E spec (T2.5).
 *
 * Drives the login → propose → review → approve loop end-to-end:
 *   1. Mint editor + maintainer sessions directly in the test DB
 *      (skips the real magic-link/email path; mirrors PHP-side pattern
 *      in tests/php/IntegrationTestCase.php → seedEditorSession).
 *   2. As the editor: GET /propose.php, submit a description tweak,
 *      verify the change_request row landed pending.
 *   3. As the maintainer: GET /review.php list + detail, POST approve,
 *      verify (a) reach.description updated, (b) edit_history row
 *      written, (c) change_request.status='approved'.
 *
 * The PHP server is shared with smoke.spec.ts (one global-setup boot);
 * EDITOR_FEATURE=1 is set there so /propose.php and /review.php exist.
 * Each test mints its own editor + maintainer emails so collisions
 * across runs are impossible.
 *
 * Magic-link bypass rationale: spinning up a real SMTP catcher (mailpit
 * etc.) for one E2E spec is more moving parts than the test is worth.
 * The magic-link path itself is covered by the PHP-side
 * `AuthMagicLinkTest`; this spec verifies the *editor cookie → request
 * gate → DB update* loop assuming the cookie exists.
 */

const DB_PATH = (() => {
  const p = process.env.KAYAK_TEST_DBPATH;
  if (!p) throw new Error('KAYAK_TEST_DBPATH not set — global-setup must run first');
  return p;
})();

/**
 * Run one or more SQL statements via the sqlite3 CLI against the test DB.
 *
 * `.timeout 30000` gives the CLI a 30 s busy-timeout matching the PHP server
 * (src/kayak/web/php/includes/db.php:53). The server holds the DB open (WAL + 30 s timeout)
 * while a test reads it through this *separate* sqlite3 process; with the CLI's
 * default busy-timeout of 0, `-bail` aborts the instant the server holds a
 * write lock ("database is locked (5)") — a timing flake that reds main on an
 * unlucky interleave even though the content is unchanged. Set via `-cmd`, not
 * a `PRAGMA busy_timeout=N` prepended to the SQL: the PRAGMA's assignment form
 * prints the value as a stray result row that would corrupt callers parsing
 * the output (e.g. the change_request `split('|')` below).
 */
function sqliteExec(sql: string): string {
  return execFileSync('sqlite3', ['-bail', '-cmd', '.timeout 30000', DB_PATH], {
    input: sql,
    encoding: 'utf8',
  });
}

/** Mint an editor row + a 7-day editor_session row + a CSRF token. */
function seedEditorSession(
  email: string,
  status: 'pending' | 'minimal' | 'full' | 'maintainer' | 'banned',
): { editorId: number; sessionToken: string; csrfToken: string } {
  const sessionToken = randomBytes(32).toString('hex');
  const sessionHash = createHash('sha256').update(sessionToken).digest('hex');
  const csrfToken = randomBytes(32).toString('hex');

  // sqlite3 -bail aborts on the first error; chain inserts then a
  // SELECT to fish the editor_id back to Node. Wrapping in
  // `BEGIN; … COMMIT;` is defensive against partial seeds.
  const out = sqliteExec(`
    BEGIN;
    INSERT INTO editor (email, status, created_at)
      VALUES ('${email}', '${status}', datetime('now'));
    INSERT INTO editor_session (editor_id, token_hash, expires_at, last_seen_at)
      VALUES (last_insert_rowid(), '${sessionHash}',
              datetime('now', '+7 days'), datetime('now'));
    SELECT id FROM editor WHERE email = '${email}';
    COMMIT;
  `);
  const editorId = parseInt(out.trim().split('\n').pop() ?? '0', 10);
  if (!editorId) throw new Error(`seedEditorSession: no id returned for ${email}`);
  return { editorId, sessionToken, csrfToken };
}

/** Insert a Reach row the editor will propose against. Returns the new id. */
function seedReach(name: string, description: string): number {
  // Matches the insert shape used by tests/php/DescriptionIntegrationTest;
  // ``no_show`` is the public-visibility flag (NOT ``is_hidden``).
  const out = sqliteExec(`
    INSERT INTO reach (name, sort_name, display_name, river, description, no_show)
      VALUES ('${name}', '${name.toLowerCase()}', '${name}', 'Test River', '${description}', 0);
    SELECT last_insert_rowid();
  `);
  return parseInt(out.trim(), 10);
}

/** Apply the editor's cookies to the browser context so /propose.php accepts us. */
async function loginAs(
  context: BrowserContext,
  auth: { sessionToken: string; csrfToken: string },
): Promise<void> {
  await context.clearCookies();
  await context.addCookies([
    {
      name: 'ed_sess',
      value: auth.sessionToken,
      url: 'http://127.0.0.1:8000',
      httpOnly: true,
    },
    {
      name: 'ed_csrf',
      value: auth.csrfToken,
      url: 'http://127.0.0.1:8000',
      httpOnly: false,
    },
  ]);
}

test.describe.serial('editor journey: login → propose → review → approve', () => {
  // Unique values per process so re-runs against the same DB don't collide.
  const stamp = Date.now();
  const editorEmail = `editor-${stamp}@example.com`;
  const maintEmail = `maint-${stamp}@example.com`;
  const reachName = `T2.5 Test Reach ${stamp}`;
  const approvedDescription = `Approved description ${stamp}`;

  let reachId: number;
  let editorAuth: { editorId: number; sessionToken: string; csrfToken: string };
  let maintAuth: { editorId: number; sessionToken: string; csrfToken: string };
  let changeRequestId = 0;

  test.beforeAll(() => {
    reachId = seedReach(reachName, 'Original description.');
    editorAuth = seedEditorSession(editorEmail, 'full');
    maintAuth = seedEditorSession(maintEmail, 'maintainer');
  });

  test('editor can submit a proposal', async ({ page, context }) => {
    await loginAs(context, editorAuth);

    const resp = await page.goto(`/propose.php?type=reach&id=${reachId}`);
    expect(resp?.status()).toBe(200);
    // The form's <h2> announces the reach name — confirms we landed on
    // the editor view, not a 403 / 404 fallback page.
    await expect(page.locator('h2')).toContainText(reachName);

    // Submit the description tweak. The form expects target_type +
    // target_id hidden inputs, a csrf_token (matched against ed_csrf
    // cookie via double-submit), and the description textarea.
    await page.fill('textarea[name="description"]', approvedDescription);
    await page.click('button[type="submit"]');

    // Server-side success banner — propose_handler emits this exact
    // phrase on a clean save (src/kayak/web/php/includes/propose_handler.php:452).
    await expect(page.locator('body')).toContainText('your proposal was recorded');

    // Verify the change_request row landed pending and the payload
    // carries our description.
    const cr = sqliteExec(
      `SELECT id, status, payload_json FROM change_request
       WHERE editor_id = ${editorAuth.editorId}
       ORDER BY id DESC LIMIT 1;`,
    ).trim();
    expect(cr).not.toBe('');
    const [crIdStr, crStatus, payload] = cr.split('|');
    changeRequestId = parseInt(crIdStr, 10);
    expect(crStatus).toBe('pending');
    expect(payload).toContain(approvedDescription);
  });

  test('maintainer can review and approve', async ({ page, context }) => {
    expect(changeRequestId).toBeGreaterThan(0); // sanity vs. test isolation drift

    await loginAs(context, maintAuth);

    // List view: the editor's CR shows up under "Review queue".
    const listResp = await page.goto('/review.php');
    expect(listResp?.status()).toBe(200);
    await expect(page.locator('body')).toContainText('Review queue');
    await expect(page.locator('body')).toContainText(editorEmail);

    // Detail view: the form pre-fills the proposed description.
    const detailResp = await page.goto(`/review.php?id=${changeRequestId}`);
    expect(detailResp?.status()).toBe(200);
    await expect(page.locator('body')).toContainText('Original description.');
    await expect(page.locator('body')).toContainText(approvedDescription);

    // Approve: fill the description field (form mirrors payload as
    // reach_<field>), pick the approve action, submit.
    await page.fill('textarea[name="reach_description"]', approvedDescription);
    await page.click('button[type="submit"][value="approve"]');

    await expect(page.locator('body')).toContainText('Approved and applied');

    // Reach row picked up the new description.
    const reach = sqliteExec(
      `SELECT description FROM reach WHERE id = ${reachId};`,
    ).trim();
    expect(reach).toBe(approvedDescription);

    // CR transitioned to approved.
    const crStatus = sqliteExec(
      `SELECT status FROM change_request WHERE id = ${changeRequestId};`,
    ).trim();
    expect(crStatus).toBe('approved');

    // edit_history captured the diff for audit.
    const histCount = parseInt(
      sqliteExec(
        `SELECT COUNT(*) FROM edit_history
         WHERE target_type = 'reach' AND target_id = ${reachId}
         AND field = 'description';`,
      ).trim(),
      10,
    );
    expect(histCount).toBeGreaterThan(0);
  });
});
