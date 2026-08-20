/**
 * Behavioural tests for archguard/dashboard/static/dashboard.js.
 *
 * 2,076 lines with no test coverage at all, which is how C3 and C4 -- both
 * plainly visible on a first read -- reached production.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { loadDashboard, run } from './harness.mjs';

/** Two recorded runs: enough for the compare feature to be usable. */
function twoRuns(url) {
  if (url.startsWith('/api/v1/runs?')) {
    return {
      runs: [
        run({ job_id: 'job-1', score: 80.0, timestamp: '2026-08-20T09:00:00Z' }),
        run({ job_id: 'job-2', score: 87.5, timestamp: '2026-08-20T10:00:00Z' }),
      ],
    };
  }
  if (url.startsWith('/api/v1/runs/latest')) return run({ job_id: 'job-2' });
  if (url.startsWith('/api/v1/modules')) return { modules: { api: 90 }, edges: [] };
  if (url.startsWith('/api/v1/evolution/trends')) return {};
  if (url.startsWith('/api/v1/evolution/latest')) return { available: false };
  if (url.startsWith('/api/v1/jobs/')) return { status: 'complete', github_url: 'https://github.com/pallets/flask' };
  return {};
}

/** No runs recorded yet: the empty state. */
function noRuns(url) {
  if (url.startsWith('/api/v1/runs?')) return { runs: [] };
  if (url.startsWith('/api/v1/runs/latest')) return { empty: true };
  if (url.startsWith('/api/v1/modules')) return { empty: true, modules: {}, edges: [] };
  return {};
}

describe('C3: run comparison', () => {
  it('enables the compare button once two runs are available', async () => {
    const window = await loadDashboard({ respond: twoRuns });

    const picker = window.document.getElementById('recent-analyses-picker');
    const toggle = window.document.getElementById('compare-toggle-btn');

    assert.ok(
      picker.options.length >= 3,
      `expected a placeholder plus two runs, got ${picker.options.length}`,
    );
    assert.equal(
      toggle.disabled,
      false,
      'compare button is still disabled after two runs loaded -- the enable ' +
        'check ran on DOMContentLoaded, before the async fetch filled the picker',
    );
  });

  it('keeps the compare button disabled when only one run exists', async () => {
    const window = await loadDashboard({
      respond: (url) =>
        url.startsWith('/api/v1/runs?')
          ? { runs: [run({ job_id: 'job-1' })] }
          : twoRuns(url),
    });

    const toggle = window.document.getElementById('compare-toggle-btn');
    assert.equal(toggle.disabled, true, 'nothing to compare against');
    assert.match(toggle.title, /at least 2/i);
  });
});

describe('C4: empty state', () => {
  it('does not destroy the dashboard chrome', async () => {
    const window = await loadDashboard({ respond: noRuns });
    const { document } = window;

    // #empty-state-panel is the page-level empty state. The .empty-state class
    // is also used for per-panel ones ("No Violations"), so select precisely.
    assert.ok(
      document.getElementById('empty-state-panel'),
      'the empty state should be shown',
    );
    // Everything below was wiped by assigning innerHTML to the container that
    // holds all of it, so the 30s poll then updated elements that no longer
    // existed and the page stayed broken until a hard reload.
    assert.ok(document.querySelector('.tablist'), 'tablist destroyed');
    assert.ok(document.getElementById('violations'), 'violations panel destroyed');
    assert.ok(document.getElementById('suppressions'), 'suppressions panel destroyed');
    assert.ok(document.getElementById('last-updated'), 'header status destroyed');
  });

  it('recovers when data arrives after an empty state', async () => {
    const window = await loadDashboard({ respond: noRuns });

    assert.ok(window.document.getElementById('empty-state-panel'));

    // Second poll, this time with data -- exactly what the 30s interval does.
    window.fetch = (url) => {
      const body = twoRuns(String(url));
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => null },
        json: () => Promise.resolve(body),
        text: () => Promise.resolve(JSON.stringify(body)),
      });
    };
    await window.fetchData();
    await new Promise((r) => setTimeout(r, 0));

    assert.equal(
      window.document.getElementById('current-score').textContent,
      '87.5',
      'the score never rendered: the elements it writes into were destroyed',
    );
    assert.equal(
      window.document.getElementById('empty-state-panel'),
      null,
      'the page-level empty state should be gone once there is data',
    );
    assert.equal(
      window.document.querySelector('.dashboard-container').hidden,
      false,
      'the dashboard should be visible again',
    );
  });

  it('stops polling while the empty state is showing', async () => {
    const window = await loadDashboard({ respond: noRuns });
    const live = window.__intervals.filter(Boolean);
    assert.equal(live.length, 0, 'polling should be cleared with nothing to poll for');
  });
});

describe('page integrity', () => {
  it('loads without throwing', async () => {
    const window = await loadDashboard({ respond: twoRuns });
    assert.deepEqual(window.__consoleErrors, []);
  });

  it('renders the health score from the latest run', async () => {
    const window = await loadDashboard({ respond: twoRuns });
    assert.equal(window.document.getElementById('current-score').textContent, '87.5');
    assert.equal(window.document.getElementById('current-band').textContent, 'PASS');
  });
});
