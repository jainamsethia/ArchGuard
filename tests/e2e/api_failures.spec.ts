import { test, expect, type Page } from '@playwright/test';

/**
 * What a person actually sees when the dashboard's requests fail.
 *
 * The jsdom tests cover the classification and the state transitions. These
 * cover the part only a browser has: that the overlay really covers the page,
 * that the banner is really visible, and that the retry control can really be
 * reached with a keyboard.
 *
 * Everything except the auth handshake is intercepted, so the page loads
 * signed in and then its data requests fail -- which is the situation being
 * tested. `/auth/status` and `/capabilities` are left alone; failing those
 * would test a page that never initialised rather than one whose session died
 * underneath it.
 */

const DATA_ROUTES = '**/api/v1/{runs,runs/latest,modules,evolution/**,deps,watch,risk}*';

async function failDataWith(page: Page, status: number, headers: Record<string, string> = {}) {
  await page.route(DATA_ROUTES, (route) =>
    route.fulfill({
      status,
      headers: { 'content-type': 'application/json', ...headers },
      body: JSON.stringify({ detail: 'nope' }),
    }),
  );
}

test('a server failure is shown, and is not reported as an empty account', async ({ page }) => {
  await failDataWith(page, 500);
  await page.goto('/dashboard.html');

  const banner = page.locator('#api-status');
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(/couldn't load/i);

  // The distinction the whole task turns on.
  await expect(page.locator('#empty-state-panel')).toHaveCount(0);
});

test('the failure banner is announced rather than only drawn', async ({ page }) => {
  await failDataWith(page, 500);
  await page.goto('/dashboard.html');

  const banner = page.locator('#api-status');
  await expect(banner).toBeVisible();
  await expect(banner).toHaveAttribute('role', 'status');
  await expect(banner).toHaveAttribute('aria-live', 'polite');
});

test('the retry control is a real button and takes focus', async ({ page }) => {
  await failDataWith(page, 500);
  await page.goto('/dashboard.html');

  const retry = page.locator('#api-status-retry');
  await expect(retry).toBeVisible();
  await expect(retry).toHaveJSProperty('tagName', 'BUTTON');
  await retry.focus();
  await expect(retry).toBeFocused();
});

test('a rate limit says so, and quotes the wait it was given', async ({ page }) => {
  await failDataWith(page, 429, { 'Retry-After': '42' });
  await page.goto('/dashboard.html');

  const banner = page.locator('#api-status');
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(/too many requests/i);
  await expect(banner).toContainText(/42 seconds/);
});

test('a rate limit does not keep hammering the endpoint', async ({ page }) => {
  let hits = 0;
  await page.route(DATA_ROUTES, (route) => {
    hits += 1;
    return route.fulfill({
      status: 429,
      headers: { 'content-type': 'application/json', 'Retry-After': '60' },
      body: '{}',
    });
  });

  await page.goto('/dashboard.html');
  await expect(page.locator('#api-status')).toBeVisible();
  const afterFirstCycle = hits;

  // Comfortably longer than a render, comfortably shorter than the 60s the
  // server asked for. Nothing new should go out in between.
  await page.waitForTimeout(2000);

  expect(hits, 'the dashboard retried into its own rate limit').toBe(afterFirstCycle);
});

test('an expired session raises the sign-in overlay, not a second UI', async ({ page }) => {
  // The page already has one way of saying "sign in". A banner over a
  // dashboard the visitor can no longer read would be a second.
  await failDataWith(page, 401);
  await page.goto('/dashboard.html');

  await expect(page.locator('#login-overlay')).toBeVisible();
  await expect(page.locator('#login-overlay')).toContainText(/session has expired/i);
  await expect(page.locator('#api-status')).toBeHidden();
});

test('an expired session offers a way back in', async ({ page }) => {
  await failDataWith(page, 401);
  await page.goto('/dashboard.html');

  const signIn = page.locator('#sign-in-button');
  await expect(signIn).toBeVisible();
  await signIn.focus();
  await expect(signIn).toBeFocused();
});

test('an expired session does not leave the dashboard readable behind it', async ({ page }) => {
  // The overlay marks everything else `inert`, which is what keeps a stale
  // authenticated view from being read or tabbed through after the session
  // that produced it has gone.
  await failDataWith(page, 401);
  await page.goto('/dashboard.html');

  await expect(page.locator('#login-overlay')).toBeVisible();
  await expect(page.locator('#dashboard-main')).toHaveAttribute('inert', '');
});
