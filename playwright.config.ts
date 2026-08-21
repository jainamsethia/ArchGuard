import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  // Both suites, selected per-run by passing a path. testDir was
  // './tests/visual', so `playwright test tests/a11y/` matched nothing and
  // exited 0 -- a job that reports success while running no tests.
  testDir: './tests',
  testMatch: ['visual/**/*.spec.ts', 'a11y/**/*.spec.ts'],
  snapshotDir: './tests/visual/snapshots',
  // Explicit, because the default template includes {testFileDir} -- which is
  // relative to testDir. Widening testDir from './tests/visual' to './tests'
  // therefore moved every expected snapshot path, and Playwright wrote fresh
  // baselines instead of comparing against the committed ones: five visual
  // tests that passed while checking nothing.
  snapshotPathTemplate: '{snapshotDir}/{testFileName}-snapshots/{arg}{-projectName}-{platform}{ext}',
  use: {
    baseURL: 'http://localhost:8765',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
    {
      name: 'mobile',
      use: { ...devices['iPhone SE'] },
    },
  ],
  webServer: {
    command: 'uvicorn archguard.dashboard.app:app --port 8765',
    env: {
      ARCHGUARD_MOCK_LLM: '1',
      ARCHGUARD_DASHBOARD_ALLOW_REMOTE: '1',
      // Sessions are signed now, and the module refuses to issue one without
      // a key. Obviously not a secret: it exists so the server starts.
      SESSION_SECRET: 'playwright-local-only-0123456789abcdef0123456789abcdef',
      // Playwright drives dozens of page loads from one address. At the
      // default 50/minute the later tests get a page whose own /auth/status
      // was rate-limited, so the sign-in overlay covers the controls they are
      // trying to click -- which reads as a flaky selector rather than as what
      // it is.
      ARCHGUARD_RATE_LIMIT_MAX_REQUESTS: '100000',
    },
    url: 'http://localhost:8765/health',
    reuseExistingServer: !process.env.CI,
  },
});
