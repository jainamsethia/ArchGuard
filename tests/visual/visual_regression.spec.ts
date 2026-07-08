import { test, expect } from '@playwright/test';

test('index.html renders consistently', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveScreenshot('index.png', { maxDiffPixelRatio: 0.001 });
});

test('dashboard.html renders consistently', async ({ page }) => {
  await page.goto('/dashboard.html');
  await expect(page).toHaveScreenshot('dashboard.png', { maxDiffPixelRatio: 0.001 });
});

test('submission error leaves button enabled', async ({ page }) => {
  await page.goto('/');
  await page.fill('#github-url', 'https://github.com/mock/repo');
  
  // mock the /api/jobs/validate endpoint to return success
  await page.route('/api/jobs/validate', route => {
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
  await page.route('/api/jobs', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ job_id: 'mock-job-id' })
    });
  });
  
  // mock the SSE stream to immediately send an error
  await page.route('/api/jobs/mock-job-id/stream', route => {
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
  await page.goto('/dashboard.html');

  let requestCount = 0;
  await page.route('/api/v1/advisor/ask*', async route => {
    requestCount++;
    await new Promise(r => setTimeout(r, 200)); // Keep request in flight
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'data: test\n\n'
    });
  });

  const input = page.locator('#advisor-question-input');
  const askBtn = page.locator('#gen-id-click-1e6913c1');

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
  await page.goto('/dashboard.html');

  await page.route('/api/v1/deps*', route => {
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'Internal Server Error' })
    });
  });

  const scanBtn = page.locator('#scan-deps-btn');
  await scanBtn.click();

  const statusEl = page.locator('#deps-status');
  await expect(statusEl).toHaveText('Error 500: could not scan dependencies.');
  
  await expect(scanBtn).toBeEnabled();
});
