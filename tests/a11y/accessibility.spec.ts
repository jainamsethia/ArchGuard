import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * Automated accessibility checks on both pages.
 *
 * The CSS already carries real accessibility intent -- prefers-reduced-motion,
 * prefers-contrast, :focus-visible, a light theme with checked contrast. The
 * JavaScript does not: no focus trap in the modal, no roving tabindex on the
 * tablist, no text alternative for the three canvas charts. P2-5 closes that
 * gap; this job is what stops it reopening.
 *
 * Serious and critical only, deliberately. A threshold that fails on every
 * minor finding on day one gets disabled by the second person who hits it,
 * and then it protects nothing. P2-5 can tighten it once the known gaps are
 * closed.
 */

const BLOCKING = ['serious', 'critical'];

async function scan(page, url: string) {
  await page.goto(url);
  // The sign-in overlay hides the page behind it. Wait for auth.js to settle
  // so axe scans whichever state the page actually lands in, rather than
  // racing it and scanning a half-rendered one.
  await page.waitForLoadState('networkidle');
  return new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
}

function blocking(results) {
  return results.violations.filter((v) => BLOCKING.includes(v.impact ?? ''));
}

function describe(violations) {
  return violations
    .map((v) => `${v.impact}: ${v.id} — ${v.help}\n  ${v.nodes.map((n) => n.target.join(' ')).join('\n  ')}`)
    .join('\n\n');
}

test('the submit page has no serious or critical accessibility violations', async ({ page }) => {
  const results = await scan(page, '/');
  const found = blocking(results);
  expect(found, describe(found)).toEqual([]);
});

test('the dashboard has no serious or critical accessibility violations', async ({ page }) => {
  const results = await scan(page, '/dashboard.html');
  const found = blocking(results);
  expect(found, describe(found)).toEqual([]);
});

test('the progress bar reports its value to assistive technology', async ({ page }) => {
  // A determinate bar that does not expose aria-valuenow is a determinate bar
  // only for people who can see it.
  await page.goto('/');
  const track = page.locator('#progress-track');
  await expect(track).toHaveAttribute('role', 'progressbar');
  await expect(track).toHaveAttribute('aria-valuemin', '0');
  await expect(track).toHaveAttribute('aria-valuemax', '100');
  await expect(track).toHaveAttribute('aria-valuenow', /\d+/);
});

/**
 * Force the signed-out state.
 *
 * A local instance signs the visitor in automatically (the development
 * fallback in _identity, gated on GitHub OAuth being unconfigured), so the
 * overlay never appears and every assertion about it skips. Controlling the
 * one endpoint auth.js reads exercises the real overlay code against a real
 * browser -- the server is untouched, only its answer to this one call.
 */
async function signedOut(page) {
  await page.route('**/api/v1/auth/status', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        authenticated: false,
        sign_in_available: true,
        sign_in_url: '/auth/github',
      }),
    }),
  );
}

test.describe('the sign-in overlay, signed out', () => {
  test('is a labelled modal dialog', async ({ page }) => {
    await signedOut(page);
    await page.goto('/dashboard.html');

    const overlay = page.locator('#login-overlay');
    await expect(overlay).toBeVisible();
    // Without these it is a <div> that happens to cover the screen: nothing
    // tells assistive technology the page behind it is unavailable.
    await expect(overlay).toHaveAttribute('role', 'dialog');
    await expect(overlay).toHaveAttribute('aria-modal', 'true');

    const labelledBy = await overlay.getAttribute('aria-labelledby');
    expect(labelledBy, 'the dialog has no accessible name').toBeTruthy();
    await expect(page.locator(`#${labelledBy}`)).toHaveText(/sign in/i);
  });

  test('moves focus into the dialog', async ({ page }) => {
    await signedOut(page);
    await page.goto('/dashboard.html');
    await expect(page.locator('#login-overlay')).toBeVisible();

    const inside = await page.evaluate(() => {
      const overlay = document.getElementById('login-overlay');
      return !!overlay && overlay.contains(document.activeElement);
    });
    expect(inside, 'focus stayed on the inert page behind the overlay').toBe(true);
  });

  test('announces a sign-in failure rather than only showing it', async ({ page }) => {
    // The status endpoint is unreachable: auth.js reports it in #login-error.
    await page.route('**/api/v1/auth/status', (route) => route.abort());
    await page.goto('/dashboard.html');

    const error = page.locator('#login-error');
    // role must be on the element before the text lands, or there is no live
    // region for the message to be announced from.
    await expect(error).toHaveAttribute('role', 'alert');
    await expect(error).toContainText(/could not reach/i);
  });
});

test('the sign-in overlay does not leave hidden content in the tab order', async ({ page }) => {
  // The overlay used to hide the page with `visibility: hidden`, which removes
  // it visually while leaving every control reachable by Tab and by screen
  // reader. `inert` removes it from both.
  //
  // Signed out and on a gated page, both of which this test used to get wrong.
  // It asked for `/`, which is deliberately public -- only the dashboard is
  // gated -- so the overlay was never there to inspect, and the guard below
  // turned that into a skip rather than a failure. It skipped on every run,
  // locally and in CI, while `inert` went unverified.
  await signedOut(page);
  await page.goto('/dashboard.html');
  await expect(page.locator('#login-overlay')).toBeVisible();

  // A skip here would hide exactly the regression this test exists to catch,
  // so the overlay's absence is a failure like any other.
  const inertCount = await page.locator('body > [inert]').count();
  expect(inertCount, 'content behind the overlay is still in the tab order').toBeGreaterThan(0);
});
