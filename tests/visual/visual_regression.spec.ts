import { test, expect } from '@playwright/test';

/**
 * Pixel comparisons are tagged @snapshot and run as a separate, advisory CI
 * job. The baselines are platform-specific (Playwright suffixes them -linux,
 * -win32) so they can only be generated where CI runs, and they go stale the
 * moment the UI changes on purpose -- which is not a regression and should not
 * block a merge.
 *
 * Everything else in this file drives the page and asserts behaviour. Those
 * are blocking, because a broken control is a bug wherever it is found.
 */
test('index.html renders consistently @snapshot', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveScreenshot('index.png', { maxDiffPixelRatio: 0.001 });
});

test('dashboard.html renders consistently @snapshot', async ({ page }) => {
  await page.goto('/dashboard.html');
  await expect(page).toHaveScreenshot('dashboard.png', { maxDiffPixelRatio: 0.001 });
});

test('submission error leaves button enabled', async ({ page }) => {
  await page.goto('/');
  await page.fill('#github-url', 'https://github.com/mock/repo');
  
  // mock the /api/jobs/validate endpoint to return success
  await page.route('/api/v1/jobs/validate', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        full_name: 'mock/repo',
        language: 'Python',
        stars: 10
      })
    });
  });

  // trigger validation
  await page.dispatchEvent('#github-url', 'input');
  
  // Wait for the button to transition to "Start Analysis"
  const submitBtn = page.locator('#btn-submit');
  await expect(submitBtn).toHaveText('Start Analysis');
  
  // mock the /api/jobs endpoint to return a job id
  await page.route('/api/v1/jobs', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: 'mock-job-id' })
    });
  });
  
  // mock the SSE stream to immediately send an error
  await page.route('/api/v1/jobs/mock-job-id/stream', route => {
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'data: {"type": "error", "error": "simulated failure"}\n\n'
    });
  });
  
  await submitBtn.click();
  
  // wait for it to process the error
  await expect(submitBtn).toHaveText('Analyze Repository');
  await expect(submitBtn).toBeEnabled();
});


/**
 * Open the dashboard with data, so its controls are actually on screen.
 *
 * `/dashboard.html` with no job_id renders the empty state and hides
 * `.dashboard-container` -- that is the C4 fix working, not a bug. Tests that
 * drive controls inside the container have to give it something to render, or
 * they wait thirty seconds for an element that is deliberately not there.
 */
const JOB_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';

async function openDashboardWithData(page) {
  const run = {
    job_id: JOB_ID,
    timestamp: '2026-01-01T00:00:00Z',
    repo_url: 'https://github.com/mock/repo',
    score: 82.0,
    grade: 'B',
    band: 'PASS',
    violations: [],
    module_scores: { core: 82.0 },
    modules_analyzed: ['core'],
    import_edges: [],
    metrics: {},
  };
  await page.route('**/api/v1/runs/latest*', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(run) }));
  await page.route('**/api/v1/runs?*', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ runs: [run], total: 1 }) }));
  await page.route('**/api/v1/modules*', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ modules: { core: 82.0 }, edges: [] }) }));
  await page.route('**/api/v1/evolution/**', (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ insufficient_history: true, runs_available: 1, runs_required: 2, message: 'not enough' }) }));
  await page.goto(`/dashboard.html?job_id=${JOB_ID}`);
  await page.waitForSelector('.dashboard-container:not([hidden])');
}

test('dashboard deep linking opens correct tab', async ({ page }) => {
  await page.goto('/dashboard.html#dependencies');
  
  const depsTab = page.locator('.tab[aria-controls="dependencies"]');
  await expect(depsTab).toHaveAttribute('aria-selected', 'true');
  
  const depsPanel = page.locator('#dependencies');
  await expect(depsPanel).toHaveClass(/active/);
  
  const overviewTab = page.locator('.tab[aria-controls="overview"]');
  await expect(overviewTab).toHaveAttribute('aria-selected', 'false');
});

test('advisor duplicate ask prevents multiple overlapping requests', async ({ page }) => {
  let requestCount = 0;
  await page.route('**/api/v1/advisor/ask*', async route => {
    requestCount++;
    await new Promise(r => setTimeout(r, 200)); // Keep request in flight
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'data: test\n\n'
    });
  });

  await openDashboardWithData(page);

  const input = page.locator('#advisor-question-input');
  // #btn-advisor-ask, not the generated id this used to name. That id came
  // from a code-generation pass and no longer exists in the template, so this
  // test had been waiting thirty seconds for an element that was not there --
  // invisible because the job was continue-on-error.
  const askBtn = page.locator('#btn-advisor-ask');

  await input.fill('What are my violations?');

  // Trigger multiple asks rapidly
  await askBtn.click();
  await input.press('Enter');
  await askBtn.click();

  // Wait for the request to complete
  await expect(askBtn).toBeEnabled();

  // Only one request should have gone through
  expect(requestCount).toBe(1);
});

test('dependency scan handles 500 error properly', async ({ page }) => {
  await page.route('**/api/v1/deps*', route => {
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'Internal Server Error' })
    });
  });

  await openDashboardWithData(page);

  const scanBtn = page.locator('#scan-deps-btn');
  await scanBtn.click();

  const statusEl = page.locator('#deps-status');
  await expect(statusEl).toHaveText('Error 500: could not scan dependencies.');
  
  await expect(scanBtn).toBeEnabled();
});
