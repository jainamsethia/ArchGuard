/**
 * Loading states announced to assistive technology (P2-5).
 *
 * Several regions on this dashboard are replaced asynchronously: the AI
 * Advisor streams an answer token by token, the remediation panels wait on a
 * model, the suppressions table waits on a request. Visually each says
 * "Loading..." or shows a blinking cursor. To a screen reader they said
 * nothing -- the region simply changed under the user with no indication that
 * anything was in flight, and a streamed answer either announced every partial
 * token or nothing at all.
 *
 * aria-busy is the fix for both halves: assistive technology holds
 * announcements for a subtree while it is true, then reads the finished result
 * once it clears. It is only meaningful paired with a live region, so the ones
 * carrying a result the user is waiting on are marked aria-live="polite".
 *
 * WCAG 4.1.3 Status Messages.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { loadDashboard, run } from './harness.mjs';

function respond(url) {
  if (url.startsWith('/api/v1/runs?')) return { runs: [run()] };
  if (url.startsWith('/api/v1/runs/latest')) return run();
  if (url.startsWith('/api/v1/modules')) return { modules: {}, edges: [] };
  if (url.startsWith('/api/v1/suppressions')) return { suppressions: [] };
  if (url.startsWith('/api/v1/remediation/plan')) return { tasks: [] };
  return {};
}

describe('P2-5: async regions are live regions', () => {
  it('marks the advisor response as a polite live region', async () => {
    const window = await loadDashboard({ respond });
    const el = window.document.getElementById('advisor-response');
    assert.equal(
      el.getAttribute('aria-live'),
      'polite',
      'the streamed answer is never announced -- the region just changes silently',
    );
  });

  it('marks both remediation result regions as polite live regions', async () => {
    const window = await loadDashboard({ respond });
    for (const id of ['remediation-results', 'violations-remediation-results']) {
      assert.equal(
        window.document.getElementById(id).getAttribute('aria-live'),
        'polite',
        `#${id} is replaced asynchronously but never announced`,
      );
    }
  });
});

describe('P2-5: aria-busy tracks work in flight', () => {
  it('marks the advisor busy while the answer is streaming', async () => {
    const window = await loadDashboard({ respond });
    const { sendAdvisorQuestion } = await window.module('features/advisor.js');
    const el = window.document.getElementById('advisor-response');

    window.document.getElementById('advisor-question-input').value = 'why is coupling high?';

    // Not awaited: everything up to the first await runs synchronously, so
    // this is the in-flight state. Without aria-busy, a live region announces
    // each partial token as it streams.
    const inFlight = sendAdvisorQuestion();
    assert.equal(el.getAttribute('aria-busy'), 'true', 'the region was not marked busy');

    await inFlight;
    assert.equal(el.getAttribute('aria-busy'), 'false', 'the busy flag was never cleared');
  });

  it('clears the advisor busy flag even when the request fails', async () => {
    const window = await loadDashboard({
      respond: (url) => (url.includes('/advisor/ask') ? null : respond(url)),
    });
    const { sendAdvisorQuestion } = await window.module('features/advisor.js');
    const el = window.document.getElementById('advisor-response');

    window.document.getElementById('advisor-question-input').value = 'anything';
    await sendAdvisorQuestion();

    // A region left permanently busy is worse than one never marked: assistive
    // technology goes on suppressing announcements for it forever.
    assert.equal(el.getAttribute('aria-busy'), 'false');
  });

  it('marks the suppressions table busy while it loads', async () => {
    const window = await loadDashboard({ respond });
    const { loadSuppressions } = await window.module('render/suppressions.js');
    const tbody = window.document.querySelector('#suppressionsTable tbody');

    const inFlight = loadSuppressions();
    assert.equal(tbody.getAttribute('aria-busy'), 'true');

    await inFlight;
    assert.equal(tbody.getAttribute('aria-busy'), 'false');
  });

  it('clears the suppressions busy flag on the early-return paths', async () => {
    // 404 is a real path here -- suppressions are bound to a workspace, and
    // the handler returns early inside the try block rather than falling
    // through to the end, which is exactly where a busy flag gets stranded.
    const window = await loadDashboard({
      respond: (url) => (url.startsWith('/api/v1/suppressions') ? null : respond(url)),
    });
    const { loadSuppressions } = await window.module('render/suppressions.js');
    const tbody = window.document.querySelector('#suppressionsTable tbody');

    await loadSuppressions();
    assert.equal(tbody.getAttribute('aria-busy'), 'false');
  });

  it('marks the remediation panel busy while the plan is generated', async () => {
    const window = await loadDashboard({ respond });
    const { generateRemediationPlan } = await window.module('features/remediation.js');
    const el = window.document.getElementById('remediation-results');

    const inFlight = generateRemediationPlan();
    assert.equal(el.getAttribute('aria-busy'), 'true');

    await inFlight;
    assert.equal(el.getAttribute('aria-busy'), 'false');
  });
});
