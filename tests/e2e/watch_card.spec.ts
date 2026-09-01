import { test, expect, type Page } from '@playwright/test';

/**
 * The watched-repository card, driven the way a person drives it.
 *
 * The jsdom tests cover each transition in isolation. This is the journey:
 * watch a repository, see what was saved, change it, see the change persist,
 * remove it, and end up back where you started. Each step reads what the
 * *previous* request stored rather than what the page hoped it stored, because
 * the whole class of bug here is a card reporting its own intentions.
 *
 * The watch API is served from an in-test store so the sequence is real -- a
 * PATCH genuinely changes what the next GET returns -- without needing a
 * worker, a schedule or a second account.
 */

const JOB_ID = '11111111-2222-4333-8444-555555555555';
const REPO = 'https://github.com/mock/repo';

/** A dashboard with one run, plus a watch API backed by a mutable row. */
async function openDashboard(page: Page) {
  const run = {
    job_id: JOB_ID,
    timestamp: '2026-01-01T00:00:00Z',
    repo_url: REPO,
    score: 82.0,
    grade: 'B',
    band: 'PASS',
    violations: [],
    module_scores: { core: 82.0 },
    modules_analyzed: ['core'],
    import_edges: [],
    metrics: {},
  };

  // The row the endpoints below read and write. `null` means not watched.
  const store: { watch: Record<string, unknown> | null } = { watch: null };
  let nextId = 41;

  await page.route('**/api/v1/runs/latest*', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(run) }));
  await page.route('**/api/v1/runs?*', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: [run], total: 1 }) }));
  await page.route('**/api/v1/modules*', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ modules: { core: 82.0 }, edges: [] }) }));
  await page.route('**/api/v1/evolution/**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ insufficient_history: true, runs_available: 1, runs_required: 2, message: 'not enough' }) }));

  await page.route('**/api/v1/watch**', async (route) => {
    const request = route.request();
    const method = request.method();
    const json = (status: number, body: unknown) =>
      route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });

    if (method === 'GET') {
      return json(200, { watched: store.watch ? [store.watch] : [] });
    }
    if (method === 'POST') {
      const body = request.postDataJSON() ?? {};
      store.watch = {
        id: nextId++,
        repo_url: REPO,
        active: true,
        health_drop_threshold: body.health_drop_threshold,
        has_webhook: Boolean(body.webhook_url),
        last_checked_at: null,
        last_status: null,
      };
      return json(201, { watched: store.watch });
    }
    if (method === 'PATCH') {
      if (!store.watch) return json(404, { detail: 'Watched repository not found' });
      const body = request.postDataJSON() ?? {};
      // Absent means unchanged, exactly as update_watched treats it.
      for (const key of ['active', 'health_drop_threshold']) {
        if (body[key] !== undefined && body[key] !== null) store.watch[key] = body[key];
      }
      if (body.webhook_url) store.watch.has_webhook = true;
      return json(200, { watched: store.watch });
    }
    if (method === 'DELETE') {
      if (!store.watch) return json(404, { detail: 'Watched repository not found' });
      store.watch = null;
      return route.fulfill({ status: 204, body: '' });
    }
    return json(405, {});
  });

  await page.goto(`/dashboard.html?job_id=${JOB_ID}`);
  await page.waitForSelector('.dashboard-container:not([hidden])');
  await expect(page.locator('#watch-card')).toBeVisible();
}

const status = (page: Page) => page.locator('#watch-status');


test('watch, configure, and stop watching', async ({ page }) => {
  await openDashboard(page);

  // --- not watched -------------------------------------------------------
  await expect(status(page)).toContainText(/not watched/i);
  await expect(page.locator('#watch-unwatch-btn')).toBeHidden();
  await expect(page.locator('#watch-save-btn')).toBeHidden();
  await expect(page.locator('#watch-threshold')).toHaveValue('');

  // --- watch it ----------------------------------------------------------
  await page.locator('#watch-threshold').fill('3');
  await page.locator('#watch-toggle-btn').click();

  await expect(status(page)).toContainText(/^Watched\./);
  await expect(status(page)).toContainText(/more than 3\./);
  await expect(page.locator('#watch-unwatch-btn')).toBeVisible();
  await expect(page.locator('#watch-toggle-btn')).toHaveText(/pause watching/i);

  // --- change the threshold, and confirm it was stored --------------------
  await page.locator('#watch-threshold').fill('7.5');
  await page.locator('#watch-save-btn').click();
  await expect(status(page)).toContainText(/more than 7\.5\./);

  // Read it back from the server rather than from the page: reload, so the
  // only thing left is what the GET returns.
  await page.reload();
  await expect(page.locator('#watch-threshold')).toHaveValue('7.5');
  await expect(status(page)).toContainText(/more than 7\.5\./);

  // --- stop watching -----------------------------------------------------
  page.once('dialog', (d) => d.accept());
  await page.locator('#watch-unwatch-btn').click();

  await expect(status(page)).toContainText(/not watched/i);
  await expect(page.locator('#watch-unwatch-btn')).toBeHidden();

  // And it is gone on the server, not only on screen.
  await page.reload();
  await expect(status(page)).toContainText(/not watched/i);
});


test('pausing keeps the configuration, and resuming does not reset it', async ({ page }) => {
  // The data loss this work fixed: resuming used to go through POST, an upsert
  // that overwrites the stored webhook and threshold with whatever the blank
  // form held.
  await openDashboard(page);

  await page.locator('#watch-threshold').fill('2');
  await page.locator('#watch-webhook').fill('https://hooks.example.com/t/abc');
  await page.locator('#watch-toggle-btn').click();
  await expect(status(page)).toContainText(/sent to your webhook/i);

  await page.locator('#watch-toggle-btn').click();
  await expect(status(page)).toContainText(/^Paused\./);

  await page.locator('#watch-toggle-btn').click();
  await expect(status(page)).toContainText(/^Watched\./);
  await expect(status(page)).toContainText(/more than 2\./);
  await expect(status(page)).toContainText(/sent to your webhook/i);
});


test('removing a watch asks first, and stops if declined', async ({ page }) => {
  await openDashboard(page);

  await page.locator('#watch-threshold').fill('4');
  await page.locator('#watch-toggle-btn').click();
  await expect(status(page)).toContainText(/^Watched\./);

  page.once('dialog', (d) => d.dismiss());
  await page.locator('#watch-unwatch-btn').click();

  await expect(status(page)).toContainText(/^Watched\./);
  await expect(page.locator('#watch-unwatch-btn')).toBeVisible();
});


test('the webhook is never echoed back into the field', async ({ page }) => {
  // The server returns only has_webhook. The field is the only copy on screen
  // and the URL routinely carries a token in its path.
  await openDashboard(page);

  await page.locator('#watch-threshold').fill('5');
  await page.locator('#watch-webhook').fill('https://hooks.example.com/t/SECRET');
  await page.locator('#watch-toggle-btn').click();
  await expect(status(page)).toContainText(/^Watched\./);

  await expect(page.locator('#watch-webhook')).toHaveValue('');
  await page.reload();
  await expect(page.locator('#watch-webhook')).toHaveValue('');
});


test('the card reports failure rather than guessing', async ({ page }) => {
  await openDashboard(page);
  await expect(status(page)).toContainText(/not watched/i);

  // Break the endpoint and force a refresh.
  await page.route('**/api/v1/watch**', (r) =>
    r.fulfill({ status: 500, contentType: 'application/json', body: '{}' }));
  await page.reload();

  await expect(status(page)).toContainText(/couldn't check/i);
  await expect(page.locator('#watch-toggle-btn')).toBeDisabled();
  await expect(page.locator('#watch-threshold')).toBeDisabled();
});


test('the status line is announced', async ({ page }) => {
  await openDashboard(page);

  const el = page.locator('#watch-status');
  await expect(el).toHaveAttribute('role', 'status');
  await expect(el).toHaveAttribute('aria-live', 'polite');
});
