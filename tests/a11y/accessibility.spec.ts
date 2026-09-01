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

/**
 * A dashboard with data in it.
 *
 * With none, poll.js calls renderEmptyState(), which hides
 * `.dashboard-container` -- and the tablist lives inside it. That is correct:
 * there is nothing to tab through on an empty dashboard. But it makes any
 * assertion about the tabs a race against the first fetch, which is why this
 * test passed alone and failed in a full run, where the server is busier and
 * the fetch lands first.
 *
 * So the state is fixed rather than raced for.
 */
async function withOneRun(page) {
  const run = {
    job_id: 'a11y-job',
    score: 82.0,
    band: 'WATCH',
    timestamp: '2026-08-30T10:00:00Z',
    violations: [],
    module_scores: { api: 90 },
    layer_results: [],
  };
  await page.route('**/api/v1/runs?**', (r) =>
    r.fulfill({ json: { runs: [run] } }));
  await page.route('**/api/v1/runs/latest**', (r) => r.fulfill({ json: run }));
  await page.route('**/api/v1/modules**', (r) =>
    r.fulfill({ json: { modules: { api: 90 }, edges: [] } }));
  await page.route('**/api/v1/evolution/**', (r) =>
    r.fulfill({ json: { available: false, trends: [] } }));
}

test('the tablist is one tab stop, and arrows move within it', async ({ page }) => {
  // The ARIA tabs pattern. All four tabs used to sit in the page tab order,
  // and there was no arrow handling at all -- so a keyboard user paid four
  // stops to cross the bar and could not move between panels without a mouse.
  await withOneRun(page);
  await page.goto('/dashboard.html?job_id=a11y-job');
  await page.waitForLoadState('networkidle');
  await expect(page.locator('.dashboard-container')).toBeVisible();

  const tabbable = page.locator('[role="tab"][tabindex="0"]');
  await expect(tabbable).toHaveCount(1);
  await expect(page.locator('[role="tab"][tabindex="-1"]')).toHaveCount(3);

  await tabbable.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('[role="tab"]:focus')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('[role="tab"][tabindex="0"]')).toHaveCount(1);
});

test('every tabpanel is labelled by the tab that controls it', async ({ page }) => {
  // Otherwise a screen reader announces "tab panel" and nothing more.
  await page.goto('/dashboard.html');
  const tabs = page.locator('[role="tab"]');
  const count = await tabs.count();
  expect(count).toBeGreaterThan(0);

  for (let i = 0; i < count; i += 1) {
    const tab = tabs.nth(i);
    const panelId = await tab.getAttribute('aria-controls');
    const tabId = await tab.getAttribute('id');
    await expect(page.locator(`#${panelId}`)).toHaveAttribute('aria-labelledby', tabId!);
  }
});

test('each chart canvas has a name and a text description', async ({ page }) => {
  // A bare <canvas> is an empty box to assistive technology.
  await page.goto('/dashboard.html');
  const canvases = page.locator('canvas');
  const count = await canvases.count();
  expect(count).toBeGreaterThanOrEqual(3);

  for (let i = 0; i < count; i += 1) {
    const canvas = canvases.nth(i);
    await expect(canvas).toHaveAttribute('role', 'img');
    const name = await canvas.getAttribute('aria-label');
    expect(name, `canvas ${i} has no accessible name`).toBeTruthy();
    const describedBy = await canvas.getAttribute('aria-describedby');
    expect(describedBy, `canvas ${i} has no description target`).toBeTruthy();
    await expect(page.locator(`#${describedBy}`)).toHaveCount(1);
  }
});

test('the dashboard reports when it is busy refreshing', async ({ page }) => {
  // aria-busy is the only signal a screen reader gets that five panels are
  // mid-update; the spinner is purely visual.
  await page.goto('/dashboard.html');
  await page.waitForLoadState('networkidle');
  // After the initial load has settled it must be false, not left stuck on.
  await expect(page.locator('#dashboard-main')).toHaveAttribute('aria-busy', 'false');
});


test('the API failure banner has no serious or critical violations', async ({ page }) => {
  // A state axe had never seen: it only exists once a request fails, so a
  // scan of the healthy dashboard never reaches it. Contrast on the banner,
  // the retry button's name, and the live region's role are all things that
  // would otherwise ship unchecked.
  await page.route('**/api/v1/{runs,runs/latest,modules,evolution/**}*', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: '{}' }),
  );
  await page.goto('/dashboard.html');
  await expect(page.locator('#api-status')).toBeVisible();

  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  const found = blocking(results);
  expect(found, describe(found)).toEqual([]);
});


test('the failure banner does not rely on colour alone', async ({ page }) => {
  // A coloured border is not a message. Someone who cannot distinguish it from
  // the card behind it must still be told what happened and what to do.
  await page.route('**/api/v1/{runs,runs/latest,modules,evolution/**}*', (route) =>
    route.fulfill({ status: 500, contentType: 'application/json', body: '{}' }),
  );
  await page.goto('/dashboard.html');

  const banner = page.locator('#api-status');
  await expect(banner).toContainText(/couldn't load/i);
  await expect(banner.locator('.api-status-title')).not.toBeEmpty();
  await expect(banner.locator('.api-status-body')).not.toBeEmpty();
});
