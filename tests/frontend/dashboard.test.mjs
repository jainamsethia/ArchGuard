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

describe('asset delivery', () => {

  it('the graph library is fetched on demand, not on page load', async () => {
  // 628.7 KB, 63% of this page's payload, for a tab most visitors never open.
  // It used to be a blocking <script> in the template.
  const { window } = await loadDashboard();

  const before = [...window.document.querySelectorAll('script[src]')]
    .filter((s) => s.src.includes('vis-network'));
  assert.equal(before.length, 0, 'vis-network was loaded on page load');

  delete window.vis;              // force the loader down its fetching path
  window.loadGraphLibrary();

  const after = [...window.document.querySelectorAll('script[src]')]
    .filter((s) => s.src.includes('vis-network'));
  assert.equal(after.length, 1, 'the loader did not inject the library');
  });

  it('a second request reuses the in-flight download', async () => {
  // Rapid tab switching must not start the 628 KB download twice.
  const { window } = await loadDashboard();
  delete window.vis;

  window.loadGraphLibrary();
  window.loadGraphLibrary();
  window.loadGraphLibrary();

  const tags = [...window.document.querySelectorAll('script[src]')]
    .filter((s) => s.src.includes('vis-network'));
  assert.equal(tags.length, 1, `started ${tags.length} downloads`);
  });

  it('polling stops while the tab is hidden and resumes when it returns', async () => {
    // Five endpoints every thirty seconds, forever, in a tab nobody is looking
    // at, is work the server does for no reader.
    const { window } = await loadDashboard();

    // Spied rather than exposed: counting timers keeps this test out of the
    // production API, which should not grow an accessor to be observable.
    let live = 0;
    const realSet = window.setInterval;
    const realClear = window.clearInterval;
    window.setInterval = (...a) => { live += 1; return realSet(...a); };
    window.clearInterval = (...a) => { live -= 1; return realClear(...a); };

    let hidden = false;
    Object.defineProperty(window.document, 'hidden', {
      get: () => hidden,
      configurable: true,
    });

    // The page starts polling as it loads, so clear that first -- otherwise
    // startPolling() sees a live handle and returns without creating one.
    window.stopPolling();
    live = 0;

    window.startPolling();
    assert.equal(live, 1, 'polling did not start on a visible tab');

    hidden = true;
    window.document.dispatchEvent(new window.Event('visibilitychange'));
    assert.equal(live, 0, 'polling continued in a hidden tab');

    hidden = false;
    window.document.dispatchEvent(new window.Event('visibilitychange'));
    assert.equal(live, 1, 'polling did not resume when the tab returned');
  });

  it('a tab hidden before polling starts does not start a timer', async () => {
    const { window } = await loadDashboard();
    let live = 0;
    const realSet = window.setInterval;
    window.setInterval = (...a) => { live += 1; return realSet(...a); };
    Object.defineProperty(window.document, 'hidden', {
      get: () => true,
      configurable: true,
    });

    window.stopPolling();
    live = 0;

    window.startPolling();
    assert.equal(live, 0, 'a hidden tab started a polling timer');
  });
});
