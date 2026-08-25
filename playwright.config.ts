import { spawnSync } from 'node:child_process';
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
 *
 * Poetry is asked as well as `./.venv`, because this is a Poetry project and
 * Poetry only writes its virtualenv in-tree when `virtualenvs.in-project` is
 * set. CI sets it, which is the only reason the in-tree lookup ever succeeded;
 * a development machine generally does not, and the virtualenv then lives in
 * the Poetry cache under the user profile, where no amount of looking at
 * `__dirname` will find it. On such a machine every candidate here missed and
 * the function fell through to its old `return 'python'`.
 *
 * Candidates are accepted by importing what uvicorn is about to import, rather
 * than by testing that a file exists, because a system interpreter satisfies
 * "exists" perfectly well while holding none of this project's dependencies.
 * The import list names the runtime dependencies explicitly and deliberately:
 * `import archguard.dashboard.app` alone succeeds on such an interpreter, since
 * the database engine is not built until it is first used. That is exactly how
 * this failed in practice -- uvicorn started, answered the /health probe, and
 * raised ModuleNotFoundError for asyncpg only on the first request that reached
 * the database, so the accessibility suite passed 12 of 12 against a server
 * that could not serve the application. One more entry in this file's list of
 * things that passed while checking nothing.
 */
const PROBE = 'import uvicorn, asyncpg, archguard.dashboard.app';

/** Whether `exe` can actually serve the dashboard, as opposed to merely existing. */
function canServe(exe: string): boolean {
  // No `shell`: the path may contain spaces, and a shell would re-split it.
  return (
    spawnSync(exe, ['-c', PROBE], {
      cwd: __dirname,
      timeout: 120_000,
      windowsHide: true,
    }).status === 0
  );
}

/** The interpreter Poetry manages for this project, if Poetry can name one. */
function poetryPython(): string | undefined {
  // `shell` on Windows, where `poetry` is a `.cmd` shim that spawnSync cannot
  // execute directly -- without it this is ENOENT on precisely the machines
  // whose virtualenv is out of tree, which are the ones that need it.
  const found = spawnSync('poetry', ['env', 'info', '--executable'], {
    cwd: __dirname,
    encoding: 'utf8',
    timeout: 120_000,
    shell: process.platform === 'win32',
    windowsHide: true,
  });
  if (found.status !== 0) return undefined;
  const path = (found.stdout ?? '').trim();
  return path === '' ? undefined : path;
}

function resolvePython(): string {
  // An interpreter that was named explicitly is used or reported, never
  // silently passed over in favour of another one: quietly serving from
  // somewhere the caller did not ask for is the failure this file keeps having.
  const named = process.env.ARCHGUARD_PYTHON;
  if (named) {
    if (canServe(named)) return named;
    throw new Error(
      `ARCHGUARD_PYTHON is set to ${named}, which cannot import the dashboard ` +
        `and its dependencies (${PROBE}). Point it at the interpreter holding ` +
        `this project's dependencies, or unset it to search for one.`,
    );
  }

  // Absolute and native-separated. The command runs through a shell, and on
  // Windows that is cmd.exe, which reads a leading `.venv/Scripts/...` as a
  // switch rather than a path -- the first attempt at this failed with
  // "'.venv' is not recognized as an internal or external command".
  const candidates = ['.venv/Scripts/python.exe', '.venv/bin/python']
    .map((candidate) => resolve(__dirname, candidate))
    .filter((full) => existsSync(full));

  const fromPoetry = poetryPython();
  if (fromPoetry) candidates.push(fromPoetry);

  for (const candidate of candidates) {
    if (canServe(candidate)) return candidate;
  }

  throw new Error(
    "No Python interpreter holding this project's dependencies was found, so " +
      'the dashboard cannot be started. ' +
      (candidates.length > 0
        ? `Tried: ${candidates.join(', ')}. `
        : 'Neither ./.venv nor Poetry offered one. ') +
      'Run `poetry install --with dev`, or set ARCHGUARD_PYTHON to the ' +
      'interpreter to use.',
  );
}

// Whether the caller has promised to run the server themselves. Read here
// because it decides how hard a failed lookup is, and again by
// `reuseExistingServer` below.
const REUSING = process.env.PLAYWRIGHT_REUSE_SERVER === '1';

/**
 * Playwright builds `webServer.command` eagerly, so the lookup cannot be
 * deferred to the moment a server is actually needed. Under
 * PLAYWRIGHT_REUSE_SERVER a failed lookup is therefore reported rather than
 * thrown: the interpreter is usually never used, and refusing to load the
 * config over it would fail well formed runs -- CI's among them, since it
 * starts its own server. If Playwright does end up starting one, the command
 * below fails with uvicorn's own import error, after this warning.
 */
function pythonForCommand(): string {
  try {
    return resolvePython();
  } catch (error) {
    if (!REUSING) throw error;
    console.warn(`[playwright.config] ${(error as Error).message}`);
    return 'python';
  }
}

const PYTHON = pythonForCommand();

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
    reuseExistingServer: REUSING,
  },
});
