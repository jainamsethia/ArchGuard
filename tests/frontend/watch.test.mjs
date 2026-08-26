/**
 * The watched-repository card.
 *
 * Two things here are worth a test rather than a click-through. The first is
 * that the webhook value never survives a successful save: it is the only copy
 * on screen, the server does not return it, and a URL with a token in its path
 * left sitting in an input is a credential on display.
 *
 * The second is that a rejected webhook URL says which part was refused. The
 * SSRF guard rejects private addresses and plain HTTP, and "Could not update"
 * with no reason is indistinguishable from a bug in our code.
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { JSDOM } from 'jsdom';

const JS = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../archguard/dashboard/static/js',
);

const CARD = `<!doctype html><body>
  <div id="watch-card" hidden>
    <p id="watch-status">Loading</p>
    <input id="watch-threshold" type="number" value="5">
    <input id="watch-webhook" type="url">
    <button id="watch-toggle-btn">Watch this repository</button>
  </div>
</body>`;

let n = 0;

/** The card in a DOM, with fetch stubbed and the run already loaded. */
async function load(responses, run = { repo_url: 'https://github.com/x/watched' }) {
  const dom = new JSDOM(CARD, { url: 'http://localhost/dashboard.html' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  globalThis.HTMLElement = dom.window.HTMLElement;

  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body });
    const next = responses.shift() || { ok: true, body: { watched: [] } };
    return {
      ok: next.ok !== false,
      status: next.status || (next.ok === false ? 400 : 200),
      json: async () => next.body,
    };
  };

  // state.js is imported WITHOUT a cache-buster on purpose: watch.js resolves
  // `../state.js` bare, and a query string is not inherited by a relative
  // import -- so busting it here would hand the test a different store object
  // than the module under test reads. The store is reset per load instead.
  const state = await import(pathToFileURL(resolve(JS, 'state.js')).href);
  state.state.latestRun = run;
  const mod = await import(
    `${pathToFileURL(resolve(JS, 'render/watch.js')).href}?watch=${n++}`
  );
  return { mod, document: dom.window.document, calls, state };
}

describe('the watch card', () => {
  it('makes no request before a run says which repository this is', async () => {
    const { mod, document, calls } = await load([], null);
    await mod.loadWatchState();

    assert.equal(calls.length, 0, 'asked about a repository the page cannot name');
    assert.equal(document.getElementById('watch-card').hidden, true);
  });

  it('reports an unwatched repository as unwatched', async () => {
    const { mod, document } = await load([{ body: { watched: [] } }]);
    await mod.loadWatchState();

    assert.equal(document.getElementById('watch-card').hidden, false);
    assert.match(document.getElementById('watch-status').textContent, /not watched/i);
    assert.match(
      document.getElementById('watch-toggle-btn').textContent,
      /watch this repository/i,
    );
  });

  it('shows the last check and status once watched', async () => {
    const { mod, document } = await load([
      {
        body: {
          watched: [
            {
              id: 1,
              repo_url: 'https://github.com/x/watched',
              active: true,
              last_checked_at: '2026-08-25T10:00:00+00:00',
              last_status: 'Health fell from 90 to 60.',
            },
          ],
        },
      },
    ]);
    await mod.loadWatchState();

    const status = document.getElementById('watch-status').textContent;
    assert.match(status, /watched/i);
    assert.match(status, /Health fell from 90 to 60/);
    assert.match(document.getElementById('watch-toggle-btn').textContent, /stop watching/i);
  });

  it('matches on repo_url, so another repository\'s watch is not shown here', async () => {
    const { mod, document } = await load([
      {
        body: {
          watched: [
            { id: 1, repo_url: 'https://github.com/x/different', active: true },
          ],
        },
      },
    ]);
    await mod.loadWatchState();
    assert.match(document.getElementById('watch-status').textContent, /not watched/i);
  });

  it('sends the threshold and webhook when watching', async () => {
    const { mod, document, calls } = await load([
      { body: { watched: [] } },
      { body: { watched: { id: 7, repo_url: 'https://github.com/x/watched', active: true } } },
    ]);
    await mod.loadWatchState();

    document.getElementById('watch-threshold').value = '2.5';
    document.getElementById('watch-webhook').value = 'https://hooks.example.com/t/abc';
    await mod.toggleWatch();

    const post = calls.find((c) => c.method === 'POST');
    assert.ok(post, 'no POST was made');
    const body = JSON.parse(post.body);
    assert.equal(body.health_drop_threshold, 2.5);
    assert.equal(body.webhook_url, 'https://hooks.example.com/t/abc');
    assert.equal(body.repo_url, 'https://github.com/x/watched');
  });

  it('clears the webhook field once it has been saved', async () => {
    const { mod, document } = await load([
      { body: { watched: [] } },
      { body: { watched: { id: 7, repo_url: 'https://github.com/x/watched', active: true } } },
    ]);
    await mod.loadWatchState();

    document.getElementById('watch-webhook').value = 'https://hooks.example.com/t/SECRET';
    await mod.toggleWatch();

    assert.equal(
      document.getElementById('watch-webhook').value,
      '',
      'a webhook URL with a token in it was left on screen',
    );
  });

  it('says why a webhook URL was refused', async () => {
    const { mod, document } = await load([
      { body: { watched: [] } },
      {
        ok: false,
        status: 400,
        body: { detail: 'Webhook URL rejected: Webhook URL must use HTTPS scheme' },
      },
    ]);
    await mod.loadWatchState();

    document.getElementById('watch-webhook').value = 'http://10.0.0.1/hook';
    await mod.toggleWatch();

    assert.match(document.getElementById('watch-status').textContent, /HTTPS/);
  });

  it('re-enables the button after a failure', async () => {
    const { mod, document } = await load([
      { body: { watched: [] } },
      { ok: false, status: 500, body: {} },
    ]);
    await mod.loadWatchState();
    await mod.toggleWatch();

    assert.equal(
      document.getElementById('watch-toggle-btn').disabled,
      false,
      'a failed save left the control permanently disabled',
    );
  });

  it('pauses rather than deletes when turned off', async () => {
    const { mod, calls } = await load([
      {
        body: {
          watched: [
            { id: 9, repo_url: 'https://github.com/x/watched', active: true },
          ],
        },
      },
      { body: {} },
    ]);
    await mod.loadWatchState();
    await mod.toggleWatch();

    const patch = calls.find((c) => c.method === 'PATCH');
    assert.ok(patch, 'turning watching off did not PATCH');
    assert.equal(patch.url, '/api/v1/watch/9');
    assert.equal(JSON.parse(patch.body).active, false);
    assert.ok(
      !calls.some((c) => c.method === 'DELETE'),
      'turning watching off deleted the watch, losing its alert history',
    );
  });
});
