/**
 * Text alternatives for the canvas charts (P2-5).
 *
 * A <canvas> is a bitmap. Chart.js draws the trend, the module scores and the
 * commit history into three of them, and to a screen reader all three were
 * nothing at all -- not "a chart", not "an image", nothing. The numbers behind
 * them existed only as pixels, so the health trend of a repository was
 * unavailable to anyone not looking at it.
 *
 * The alternative here is the data, not a description of the picture: each
 * canvas is announced as an image with a one-line summary, and carries an
 * sr-only table of the same values the chart plots.
 *
 * WCAG 1.1.1 Non-text Content.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { loadDashboard, run } from './harness.mjs';

const RUNS = [
  run({ job_id: 'job-1', score: 80.0, timestamp: '2026-08-20T09:00:00Z' }),
  run({ job_id: 'job-2', score: 87.5, timestamp: '2026-08-20T10:00:00Z' }),
];

function respond(url) {
  if (url.startsWith('/api/v1/runs?')) return { runs: RUNS };
  if (url.startsWith('/api/v1/runs/latest')) return run({ job_id: 'job-2' });
  if (url.startsWith('/api/v1/modules')) return { modules: { api: 90, core: 75 }, edges: [] };
  if (url.startsWith('/api/v1/evolution/trends')) return {};
  if (url.startsWith('/api/v1/evolution/latest')) return { available: false };
  return {};
}

/** The sr-only table a canvas points at, if the chart published one. */
function tableFor(document, canvasId) {
  return document.getElementById(`${canvasId}-table`);
}

describe('P2-5: chart text alternatives', () => {
  it('announces the trend canvas as an image with a summary', async () => {
    const window = await loadDashboard({ respond });
    const canvas = window.document.getElementById('trendChart');

    assert.equal(canvas.getAttribute('role'), 'img', 'a bare <canvas> is announced as nothing');
    const label = canvas.getAttribute('aria-label') || '';
    assert.ok(label.trim().length > 0, 'the canvas has no accessible name');
  });

  it('publishes the trend data as a table, not just pixels', async () => {
    const window = await loadDashboard({ respond });
    const table = tableFor(window.document, 'trendChart');

    assert.ok(table, 'no text alternative was rendered for the trend chart');
    assert.ok(
      table.classList.contains('sr-only'),
      'the alternative should be for assistive tech, not a second visible table',
    );

    const rows = table.querySelectorAll('tbody tr');
    assert.equal(rows.length, RUNS.length, `expected one row per run, got ${rows.length}`);

    // The actual plotted values have to be present -- a table of row labels
    // with no numbers is as useless as the canvas was.
    const text = table.textContent;
    for (const r of RUNS) {
      assert.ok(text.includes(String(r.score)), `score ${r.score} missing from the table`);
    }
  });

  it('publishes the module scores as a table', async () => {
    const window = await loadDashboard({ respond });
    const canvas = window.document.getElementById('moduleChart');
    const table = tableFor(window.document, 'moduleChart');

    assert.equal(canvas.getAttribute('role'), 'img');
    assert.ok(table, 'no text alternative for the module chart');

    const text = table.textContent;
    assert.ok(text.includes('api'), 'module name missing');
    assert.ok(text.includes('90'), 'module score missing');
  });

  it('gives every alternative table a caption', async () => {
    const window = await loadDashboard({ respond });
    for (const id of ['trendChart', 'moduleChart']) {
      const table = tableFor(window.document, id);
      const caption = table.querySelector('caption');
      assert.ok(caption && caption.textContent.trim(), `#${id}-table has no caption`);
    }
  });

  it('refreshes the table when the chart updates instead of stacking a second one', async () => {
    const window = await loadDashboard({ respond });
    const { updateTrendChart } = await window.module('render/charts.js');

    // The second call takes the update path, which returns early after
    // handing new data to the existing Chart instance. The text alternative
    // has to be refreshed on that path too, or it silently keeps showing the
    // first render's numbers forever.
    updateTrendChart([
      ...RUNS,
      run({ job_id: 'job-3', score: 42.0, timestamp: '2026-08-20T11:00:00Z' }),
    ]);

    const tables = window.document.querySelectorAll('#trendChart-table');
    assert.equal(tables.length, 1, 'a second alternative table was appended');

    const rows = tables[0].querySelectorAll('tbody tr');
    assert.equal(rows.length, 3, 'the table still shows the previous render');
    assert.ok(tables[0].textContent.includes('42'), 'the newest value is missing from the table');
  });
});
