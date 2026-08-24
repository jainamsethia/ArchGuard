/**
 * The "watch this repository" toggle (P3-1).
 *
 * Watching enrols a repository for scheduled cloning and analysis, so the
 * control has to show which state it is actually in -- not what clicking will
 * do. It is a toggle with aria-pressed rather than a button whose label flips,
 * because a label change alone announces nothing.
 *
 * The state is refreshed from the poll cycle rather than on DOMContentLoaded.
 * The compare control shipped the other way round once: its enable check ran
 * before the async fetch had filled anything in, so it stayed permanently
 * disabled. This pins that it does not happen again here.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { loadDashboard, run } from './harness.mjs';

const REPO = 'https://github.com/pallets/flask.git';

/** Responds as a dashboard with one run, and a controllable watch list. */
function respondWith(watched) {
  return (url) => {
    if (url.startsWith('/api/v1/watched')) return { watched };
    if (url.startsWith('/api/v1/runs?')) return { runs: [run({ repo_url: REPO })] };
    if (url.startsWith('/api/v1/runs/latest')) return run({ repo_url: REPO });
    if (url.startsWith('/api/v1/modules')) return { modules: {}, edges: [] };
    return {};
  };
}

const button = (window) => window.document.getElementById('watch-toggle-btn');

describe('P3-1: watch toggle state', () => {
  it('stays hidden until a run identifies a repository', async () => {
    const window = await loadDashboard({
      respond: (url) => {
        if (url.startsWith('/api/v1/runs/latest')) return { empty: true };
        if (url.startsWith('/api/v1/runs?')) return { runs: [] };
        return {};
      },
    });

    assert.equal(button(window).hidden, true, 'there is no repository to watch yet');
  });

  it('shows as pressed for a repository already being watched', async () => {
    const window = await loadDashboard({ respond: respondWith([{ repo_url: REPO }]) });
    const btn = button(window);

    assert.equal(btn.hidden, false);
    // The whole point: opening the page on a watched repository must not show
    // an unpressed toggle until the user clicks it.
    assert.equal(btn.getAttribute('aria-pressed'), 'true');
    assert.match(btn.textContent, /watching/i);
  });

  it('shows as unpressed for a repository that is not watched', async () => {
    const window = await loadDashboard({ respond: respondWith([]) });
    const btn = button(window);

    assert.equal(btn.hidden, false);
    assert.equal(btn.getAttribute('aria-pressed'), 'false');
  });

  it('ignores a watch list belonging to a different repository', async () => {
    const window = await loadDashboard({
      respond: respondWith([{ repo_url: 'https://github.com/psf/requests.git' }]),
    });
    assert.equal(button(window).getAttribute('aria-pressed'), 'false');
  });
});

describe('P3-1: watch toggle actions', () => {
  it('POSTs to start watching and flips to pressed', async () => {
    const window = await loadDashboard({ respond: respondWith([]) });
    const btn = button(window);

    btn.click();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    const call = window.__requests.filter((r) => r.url.includes('/api/v1/watched')).pop();
    assert.equal(call.init.method, 'POST');
    assert.equal(JSON.parse(call.init.body).repo_url, REPO);
    assert.equal(btn.getAttribute('aria-pressed'), 'true');
  });

  it('DELETEs to stop watching and flips to unpressed', async () => {
    const window = await loadDashboard({ respond: respondWith([{ repo_url: REPO }]) });
    const btn = button(window);
    assert.equal(btn.getAttribute('aria-pressed'), 'true');

    btn.click();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    const call = window.__requests.filter((r) => r.url.includes('/api/v1/watched')).pop();
    assert.equal(call.init.method, 'DELETE');
    assert.equal(btn.getAttribute('aria-pressed'), 'false');
  });

  it('marks itself busy while the request is in flight', async () => {
    const window = await loadDashboard({ respond: respondWith([]) });
    const btn = button(window);

    btn.click();
    // Synchronous part of the handler: disabled and busy before the await.
    assert.equal(btn.disabled, true, 'a second click could double-toggle');
    assert.equal(btn.getAttribute('aria-busy'), 'true');

    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    assert.equal(btn.disabled, false);
    assert.equal(btn.getAttribute('aria-busy'), 'false');
  });

  it('does not flip state when the request fails', async () => {
    const window = await loadDashboard({
      respond: (url) => {
        // null makes the harness answer 404.
        if (url.startsWith('/api/v1/watched')) return null;
        return respondWith([])(url);
      },
    });
    const btn = button(window);
    const before = btn.getAttribute('aria-pressed');

    btn.click();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));

    // Showing "Watching" for a watch the server rejected is worse than showing
    // nothing: the user believes a scheduled scan exists that does not.
    assert.equal(btn.getAttribute('aria-pressed'), before);
    assert.equal(btn.disabled, false, 'the control was left stuck');
  });
});
