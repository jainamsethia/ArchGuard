import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/visual',
  snapshotDir: './tests/visual/snapshots',
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
    env: { ARCHGUARD_MOCK_LLM: '1', ARCHGUARD_DASHBOARD_ALLOW_REMOTE: '1' },
    url: 'http://localhost:8765/health',
    reuseExistingServer: !process.env.CI,
  },
});
