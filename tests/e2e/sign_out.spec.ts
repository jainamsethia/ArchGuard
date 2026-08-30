import { test, expect } from '@playwright/test';

/**
 * Signing out, in a real browser.
 *
 * The jsdom tests cover the wiring and the Python tests cover the server
 * ending the session. Neither can cover the two things that only exist in a
 * browser: where the page actually goes, and whether the protected page is
 * still readable afterwards.
 *
 * On an instance with no OAuth app configured, `dev_login_permitted` signs
 * every loopback request in as the local development account -- so signing out
 * genuinely does end the session, and the next request starts a new one. That
 * is deliberate (production refuses to boot without OAuth), and it means this
 * file asserts what sign-out *does* rather than that the visitor stays signed
 * out afterwards, which is a property only a configured instance has.
 */

test('the dashboard offers a way out of the session', async ({ page }) => {
  await page.goto('/dashboard.html');

  const signOut = page.locator('#sign-out-button');
  await expect(signOut).toBeVisible();
  // A button, not a link: it changes state, and a link would be followed by
  // prefetchers.
  await expect(signOut).toHaveJSProperty('tagName', 'BUTTON');
});

test('the dashboard has a way back to the rest of the site', async ({ page }) => {
  // It had none: no anchor of any kind, so a signed-in visitor who wanted to
  // analyse something else had to edit the URL.
  await page.goto('/dashboard.html');
  await expect(page.locator('.account-bar-dashboard a[href="/"]')).toBeVisible();
});

test('signing out calls the endpoint that ends the session, and leaves', async ({ page }) => {
  await page.goto('/dashboard.html');

  const logout = page.waitForRequest(
    (r) => r.url().includes('/api/v1/auth/logout') && r.method() === 'POST',
  );
  await page.locator('#sign-out-button').click();
  await logout;

  // Off the protected page entirely, so nothing rendered under the old session
  // is left on screen.
  await page.waitForURL((url) => !url.pathname.includes('dashboard'));
  expect(page.url()).not.toContain('dashboard.html');
});

test('the session that was signed out of is not accepted again', async ({ page, context }) => {
  // The property the whole task turns on, checked against the cookie the
  // browser was actually holding rather than against a fresh one.
  await page.goto('/dashboard.html');

  const before = (await context.cookies()).find((c) => c.name === 'archguard_session');
  // An instance running on the dev-login fallback authenticates each request
  // rather than issuing a cookie, so there is nothing to replay. The property
  // itself is covered against a configured instance in
  // tests/integration/test_sign_out.py -- this is the browser's confirmation,
  // not its only coverage.
  test.skip(!before, 'no session cookie: this instance uses the dev-login fallback');

  await page.locator('#sign-out-button').click();
  await page.waitForURL((url) => !url.pathname.includes('dashboard'));

  // Replay it in a context of its own, so nothing this browser did since can
  // account for the answer.
  const replay = await context.browser()!.newContext();
  await replay.addCookies([{ ...before!, name: before!.name, value: before!.value }]);
  const response = await replay.request.get('/api/v1/auth/status');
  const body = await response.json();
  await replay.close();

  expect(body.authenticated, 'the revoked session was accepted again').toBeFalsy();
});

test('the sign-out control is offered on the submit page too', async ({ page }) => {
  // Sign-out has to be reachable from wherever the visitor happens to be, not
  // only from the dashboard.
  await page.goto('/');
  await expect(page.locator('#sign-out-button')).toBeVisible();
});
