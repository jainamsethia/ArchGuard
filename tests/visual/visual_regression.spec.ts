import { test, expect } from '@playwright/test';

test('index.html renders consistently', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveScreenshot('index.png', { maxDiffPixelRatio: 0.001 });
});

test('dashboard.html renders consistently', async ({ page }) => {
  await page.goto('/dashboard.html');
  await expect(page).toHaveScreenshot('dashboard.png', { maxDiffPixelRatio: 0.001 });
});
