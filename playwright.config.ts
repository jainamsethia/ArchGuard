import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

import { defineConfig, devices } from '@playwright/test';

/**
 * The interpreter that holds the project's dependencies.
 *
 * `webServer.command` used to be a bare `uvicorn ...`, which only resolves if a
 * virtualenv is already activated -- so it never started, and every "passing"
 * run was actually `reuseExistingServer` finding whatever happened to be on
 * port 8765. During the P2-3 asset work that was a uvicorn from five hours
 * earlier, and Playwright reported its stale behaviour as three regressions in
 * the change under review.
 *
 * `python -m uvicorn` rather than the console script, because a script lives in
 * the venv's bin directory and is only on PATH once activated, while the
 * interpreter can be named directly.
 */
function resolvePython(): string {
  if (process.env.ARCHGUARD_PYTHON) return process.env.ARCHGUARD_PYTHON;
  // Absolute and native-separated. The command runs through a shell, and on
  // Windows that is cmd.exe, which reads a leading `.venv/Scripts/...` as a
  // switch rather than a path -- the first attempt at this failed with
  // "'.venv' is not recognized as an internal or external command".
  for (const candidate of ['.venv/Scripts/python.exe', '.venv/bin/python']) {
    const full = resolve(__dirname, candidate);
    if (existsSync(full)) return full;
  }
  return 'python';
}

const PYTHON = resolvePython();

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
    // 127.0.0.1, not localhost. Node resolves `localhost` to ::1 first, and
    // uvicorn binds IPv4 only, so the readiness probe below failed with
    // ECONNREFUSED ::1:8765 forever while the server answered fine on IPv4.
    baseURL: 'http://127.0.0.1:8765',
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
    command: `"${PYTHON}" -m uvicorn archguard.dashboard.app:app --host 127.0.0.1 --port 8765 --log-level warning`,
    env: {
      // Merged, not replaced. Playwright's `env` REPLACES process.env
      // wholesale, so the server was starting without PATH, SYSTEMROOT or
      // TEMP. It answered /health in milliseconds and then wedged the run --
      // a single test that passes in 3.8s against a normally-started server
      // never returned at all.
      ...process.env,
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
      // The app logs every request at INFO. Playwright captures the server's
      // output, and a chatty child filling that pipe is enough to wedge the
      // run: with this at INFO a single test never returned, while the server
      // itself answered in milliseconds throughout.
      LOG_LEVEL: 'WARNING',
    },
    // Belt and braces with LOG_LEVEL above. Nothing reads this output, and an
    // undrained pipe is a deadlock waiting for a verbose day.
    stdout: 'ignore',
    stderr: 'pipe',
    url: 'http://127.0.0.1:8765/health',
    // Opt-in, never automatic. This used to be `!process.env.CI`, so every
    // local run silently adopted whatever was listening on 8765 -- which is
    // how a uvicorn from five hours earlier came to be reported as three
    // regressions in an unrelated change. Reuse now requires someone to say so.
    //
    // Callers that start their own server (CI does, and so should you on
    // Windows -- see below) set PLAYWRIGHT_REUSE_SERVER=1. Everyone else gets
    // a server Playwright starts and stops itself.
    //
    // KNOWN ISSUE, Windows: when Playwright manages the server here, the
    // *runner* hangs -- tests begin, the server answers in milliseconds
    // throughout, and nothing completes even with a 12s per-test timeout. The
    // same tests pass in 3.8s against a server started separately. Not
    // root-caused; ruled out so far: the interpreter path, IPv4 vs IPv6 on the
    // readiness probe (which was a real bug, fixed above), stdout/stderr
    // piping, uvicorn log level, and env inheritance. Start the server
    // yourself and set PLAYWRIGHT_REUSE_SERVER=1 until it is understood.
    reuseExistingServer: process.env.PLAYWRIGHT_REUSE_SERVER === '1',
  },
});
