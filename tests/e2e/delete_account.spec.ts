import { test, expect } from '@playwright/test';

/**
 * Deleting an account, in a real browser.
 *
 * The Python tests cover what the server does -- the row goes, its jobs, runs,
 * findings, suppressions and watches go with it, and every session for it stops
 * working. The jsdom tests cover the wiring. Neither can cover the two things
 * that only exist in a browser: that a native confirmation dialog actually
 * appears, and that declining it stops the request.
 *
 * The request is intercepted rather than allowed through. This suite runs
 * against a shared dev instance where every loopback request is signed in as
 * the same local development account, so letting one test delete it would pull
 * the account out from under whatever else is running. What has to be true here
 * is that the browser asks, respects the answer, and sends the right request --
 * whether the server then does its job is tests/integration/test_account_deletion.py.
 */

const ENDPOINT = '**/api/v1/auth/account';

test('the dashboard offers a way to delete the account', async ({ page }) => {
  await page.goto('/dashboard.html');

  const del = page.locator('#delete-account-button');
  await expect(del).toBeVisible();
  // A button, not a link. A link to an irreversible action is one a prefetcher
  // or a link scanner will eventually follow on the user's behalf.
  await expect(del).toHaveJSProperty('tagName', 'BUTTON');
});

test('it asks before deleting anything, and names what goes', async ({ page }) => {
  await page.goto('/dashboard.html');

  let asked = '';
  page.on('dialog', async (dialog) => {
    asked = dialog.message();
    await dialog.dismiss();
  });

  await page.locator('#delete-account-button').click();
  await expect.poll(() => asked).toMatch(/cannot be undone/i);
  expect(asked).toMatch(/jobs|runs|findings|analysis/i);
});

test('declining the confirmation sends nothing', async ({ page }) => {
  await page.goto('/dashboard.html');

  const calls: string[] = [];
  await page.route(ENDPOINT, async (route) => {
    calls.push(route.request().method());
    await route.fulfill({ status: 200, body: '{"ok":true}' });
  });
  page.on('dialog', (dialog) => dialog.dismiss());

  await page.locator('#delete-account-button').click();
  // Long enough that a request would have been made if one were coming.
  await page.waitForTimeout(300);

  expect(calls, 'declining the confirmation still called the endpoint').toEqual([]);
  expect(page.url()).toContain('dashboard');
});

test('accepting it sends a DELETE and leaves the page', async ({ page }) => {
  await page.goto('/dashboard.html');

  const calls: string[] = [];
  await page.route(ENDPOINT, async (route) => {
    calls.push(route.request().method());
    await route.fulfill({ status: 200, body: '{"ok":true}' });
  });
  page.on('dialog', (dialog) => dialog.accept());

  await page.locator('#delete-account-button').click();

  await expect.poll(() => calls).toEqual(['DELETE']);
  await page.waitForURL((url) => !url.pathname.includes('dashboard'));
  expect(page.url()).not.toContain('dashboard.html');
});

test('a failed deletion says so instead of pretending it worked', async ({ page }) => {
  await page.goto('/dashboard.html');

  await page.route(ENDPOINT, (route) => route.fulfill({ status: 500, body: '{}' }));
  page.on('dialog', (dialog) => dialog.accept());

  await page.locator('#delete-account-button').click();

  // Still here, and told why. Redirecting on a failure would leave someone
  // believing their data was gone when every row of it is still there.
  await expect(page.locator('#login-error')).toContainText(/could not delete/i);
  expect(page.url()).toContain('dashboard');
});

test('the deletion control is reachable by keyboard', async ({ page }) => {
  // It is styled as the quietest control in the bar, which must not have made
  // it the hardest one to reach on purpose.
  await page.goto('/dashboard.html');

  const del = page.locator('#delete-account-button');
  await del.focus();
  await expect(del).toBeFocused();
});
