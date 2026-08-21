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

test('the sign-in overlay does not leave hidden content in the tab order', async ({ page }) => {
  // The overlay used to hide the page with `visibility: hidden`, which removes
  // it visually while leaving every control reachable by Tab and by screen
  // reader. `inert` removes it from both.
  await page.goto('/');
  const overlayVisible = await page.locator('#login-overlay').isVisible();
  if (!overlayVisible) {
    test.skip(true, 'no overlay shown: this instance signs in automatically');
  }
  const inertCount = await page.locator('body > [inert]').count();
  expect(inertCount).toBeGreaterThan(0);
});
