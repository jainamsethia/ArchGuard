/**
 * The "share this report" controls (P3-2).
 *
 * Sharing makes one analysis readable by anyone holding the link, so the
 * control has to be honest in both directions: a report already shared must say
 * so on load, and the button must never claim a state the server did not
 * confirm. The dangerous direction is showing "not shared" for a link that is
 * still live, because the owner then believes they have withdrawn access they
 * are still granting.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { loadDashboard, run } from './harness.mjs';

const JOB = 'job-1';
const LINK = 'http://localhost/shared/tok_abcdefghijklmnopqrstuvwxyz012345';

/** A dashboard whose latest run is shared or not, per `shared`. */
function respondWith(shared, shareResponse = { share_url: LINK }) {
  return (url) => {
    if (url.includes('/share')) return shareResponse;
    if (url.startsWith('/api/v1/runs?')) return { runs: [run({ job_id: JOB, shared })] };
    if (url.startsWith('/api/v1/runs/latest')) return run({ job_id: JOB, shared });
    if (url.startsWith('/api/v1/modules')) return { modules: {}, edges: [] };
    if (url.startsWith('/api/v1/watched')) return { watched: [] };
    return {};
  };
}

const $ = (window, id) => window.document.getElementById(id);
const settle = async () => {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
};

describe('P3-2: share control state', () => {
  it('stays hidden until a run exists', async () => {
    const window = await loadDashboard({
      respond: (url) => {
        if (url.startsWith('/api/v1/runs/latest')) return { empty: true };
        if (url.startsWith('/api/v1/runs?')) return { runs: [] };
        return {};
      },
    });
    assert.equal($(window, 'share-btn').hidden, true);
  });

  it('shows an unshared run as unpressed, with no revoke and no link', async () => {
    const window = await loadDashboard({ respond: respondWith(false) });

    assert.equal($(window, 'share-btn').hidden, false);
    assert.equal($(window, 'share-btn').getAttribute('aria-pressed'), 'false');
    assert.equal($(window, 'share-revoke-btn').hidden, true);
    assert.equal($(window, 'share-link-row').hidden, true);
  });

  it('shows an already-shared run as pressed on load', async () => {
    const window = await loadDashboard({ respond: respondWith(true) });

    assert.equal($(window, 'share-btn').getAttribute('aria-pressed'), 'true');
    assert.equal($(window, 'share-revoke-btn').hidden, false);
  });

  it('does not put the link on screen just because the run is shared', async () => {
    // Knowing a report is shared is not the same as displaying its URL to
    // whoever is looking at the monitor. The owner asks for it by clicking.
    const window = await loadDashboard({ respond: respondWith(true) });
    assert.equal($(window, 'share-link-row').hidden, true);
  });
});

describe('P3-2: sharing and revoking', () => {
  it('POSTs and reveals the returned link', async () => {
    const window = await loadDashboard({ respond: respondWith(false) });
    const btn = $(window, 'share-btn');

    btn.click();
    await settle();

    const call = window.__requests.filter((r) => r.url.includes('/share')).pop();
    assert.equal(call.init.method, 'POST');
    assert.match(call.url, /\/api\/v1\/runs\/job-1\/share/);

    assert.equal(btn.getAttribute('aria-pressed'), 'true');
    assert.equal($(window, 'share-link-row').hidden, false);
    assert.equal($(window, 'share-link-input').value, LINK);
  });

  it('DELETEs to revoke and clears the link from the page', async () => {
    const window = await loadDashboard({ respond: respondWith(true) });

    // Reveal it first, so there is something to clear.
    $(window, 'share-btn').click();
    await settle();
    assert.equal($(window, 'share-link-row').hidden, false);

    $(window, 'share-revoke-btn').click();
    await settle();

    const call = window.__requests.filter((r) => r.url.includes('/share')).pop();
    assert.equal(call.init.method, 'DELETE');
    assert.equal($(window, 'share-btn').getAttribute('aria-pressed'), 'false');
    assert.equal($(window, 'share-link-row').hidden, true, 'the link stayed on screen after revoking');
  });

  it('does not claim "not shared" when revoking failed', async () => {
    // The dangerous direction: the owner would believe they had withdrawn
    // access that is in fact still granted.
    const window = await loadDashboard({
      respond: (url) => {
        if (url.includes('/share')) return null; // harness answers 404
        return respondWith(true)(url);
      },
    });

    const revoke = $(window, 'share-revoke-btn');
    assert.equal($(window, 'share-btn').getAttribute('aria-pressed'), 'true');

    revoke.click();
    await settle();

    assert.equal(
      $(window, 'share-btn').getAttribute('aria-pressed'),
      'true',
      'the control reported the link as withdrawn when the server refused',
    );
    assert.equal(revoke.disabled, false, 'the control was left stuck');
  });

  it('does not claim "shared" when sharing failed', async () => {
    const window = await loadDashboard({
      respond: (url) => {
        if (url.includes('/share')) return null;
        return respondWith(false)(url);
      },
    });
    const btn = $(window, 'share-btn');

    btn.click();
    await settle();

    assert.equal(btn.getAttribute('aria-pressed'), 'false');
    assert.equal($(window, 'share-link-row').hidden, true);
  });

  it('marks itself busy while the request is in flight', async () => {
    const window = await loadDashboard({ respond: respondWith(false) });
    const btn = $(window, 'share-btn');

    btn.click();
    assert.equal(btn.disabled, true, 'a second click could mint twice');
    assert.equal(btn.getAttribute('aria-busy'), 'true');

    await settle();
    assert.equal(btn.disabled, false);
    assert.equal(btn.getAttribute('aria-busy'), 'false');
  });

  it('gives the link field a label rather than relying on placement', async () => {
    const window = await loadDashboard({ respond: respondWith(false) });
    const label = window.document.querySelector('label[for="share-link-input"]');
    assert.ok(label, 'the read-only link input has no accessible name');
    assert.ok(label.textContent.trim().length > 0);
  });
});
