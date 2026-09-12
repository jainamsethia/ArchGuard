/**
 * The violations table has to actually render violations.
 *
 * It did not. `applyViolationsView` sorts by severity by default and read a
 * `SEVERITY_RANK` map that was defined nowhere in the codebase -- lost in the
 * move to ES modules, and referenced only inside a sort comparator, so nothing
 * touched it until a run had two or more violations to order. Then the first
 * comparison threw ReferenceError, `updateViolationsTable` aborted, and the
 * table sat on its "Loading..." placeholder for good.
 *
 * The failure was quiet in the worst way: the throw happened inside poll.js's
 * try/catch, after the metrics had already rendered. The page showed a health
 * score, a grade and a violation count, and an empty table underneath -- so it
 * looked like a repository with nothing wrong rather than a broken page.
 *
 * Found against getredash/redash: 3 violations stored, 3 returned by the API,
 * 0 displayed.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { loadDashboard, run } from './harness.mjs';

/** A run carrying violations of differing severity, as the analysis emits them. */
function runWithViolations(overrides = {}) {
  return run({
    job_id: 'job-v',
    score: 46.4,
    violations: [
      // file is null and line is 0 on module-scoped findings: the location
      // cell falls back to the module name, and must not be assumed present.
      { module: 'redash', layer: '2', severity: 'high', file: null, line: 0,
        message: 'fan_out=101 exceeds budget=10', kind: 'fan_out' },
      { module: 'migrations.versions', layer: '4', severity: 'low', file: null, line: 0,
        message: 'duplication score 0.07', kind: 'duplication' },
      { module: 'core', layer: '1', severity: 'critical', file: 'core/service.py', line: 3,
        message: 'core must not import db', kind: 'forbidden_import' },
    ],
    ...overrides,
  });
}

function respondWith(latest) {
  return (url) => {
    if (url.startsWith('/api/v1/runs?')) return { runs: [latest] };
    if (url.startsWith('/api/v1/runs/latest')) return latest;
    if (url.startsWith('/api/v1/modules')) return { modules: {}, edges: [] };
    if (url.startsWith('/api/v1/evolution/trends')) return {};
    if (url.startsWith('/api/v1/evolution/latest')) return { available: false };
    if (url.startsWith('/api/v1/jobs/')) return { status: 'complete' };
    return {};
  };
}

describe('violations table', () => {
  it('renders every violation the API returned', async () => {
    const { document } = await loadDashboard({
      respond: respondWith(runWithViolations()),
      search: '?job_id=job-v',
    });

    const rows = [...document.querySelectorAll('#violationsTable tbody tr')];
    assert.equal(rows.length, 3, 'all three violations should have a row');
    assert.ok(
      !rows.some(r => /Loading/i.test(r.textContent)),
      'the table is still showing its loading placeholder, so rendering threw'
    );
  });

  it('orders them most severe first', async () => {
    const { document } = await loadDashboard({
      respond: respondWith(runWithViolations()),
      search: '?job_id=job-v',
    });

    const severities = [...document.querySelectorAll('#violationsTable tbody tr')]
      .map(r => r.querySelectorAll('td')[1].textContent.trim().split(/\s+/)[0]);
    assert.deepEqual(severities, ['critical', 'high', 'low']);
  });

  it('falls back to the module name when a violation has no file', async () => {
    const { document } = await loadDashboard({
      respond: respondWith(runWithViolations()),
      search: '?job_id=job-v',
    });

    const locations = [...document.querySelectorAll('#violationsTable tbody tr')]
      .map(r => r.querySelectorAll('td')[2].textContent.trim());
    assert.ok(locations.some(l => l.startsWith('core/service.py:3')), 'file:line when known');
    assert.ok(locations.some(l => l.includes('redash')), 'module name when not');
  });

  it('survives a severity the page has never heard of', async () => {
    // The server's vocabulary can grow. An unknown value must sort last, not
    // empty the table -- the whole point of the bug this file exists for.
    const latest = runWithViolations();
    latest.violations.push({
      module: 'x', layer: '2', severity: 'catastrophic', file: null, line: 0,
      message: 'from a newer server',
    });

    const { document } = await loadDashboard({
      respond: respondWith(latest),
      search: '?job_id=job-v',
    });

    const rows = [...document.querySelectorAll('#violationsTable tbody tr')];
    assert.equal(rows.length, 4);
    assert.match(rows[3].textContent, /from a newer server/);
  });
});
