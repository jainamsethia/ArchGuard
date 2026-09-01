/**
 * Completing the watched-repository card (I-6).
 *
 * The card could start watching and pause, and that was all. It could not show
 * what was configured, could not change it, and could not stop watching --
 * "Stop watching" paused, and nothing in the product ever called DELETE.
 *
 * Three of these are worth stating outright, because each was a way for the UI
 * to be wrong rather than merely incomplete:
 *
 *   - the threshold input showed the template's hardcoded 5 whatever the watch
 *     was actually set to;
 *   - resuming a paused watch went through POST, which is an upsert, so it
 *     overwrote the stored webhook and threshold with whatever the blank form
 *     held;
 *   - a failed read rendered as "Not watched", a claim about an account the
 *     request had just failed to read.
 *
 * The fixture in watch.test.mjs is shared by reimplementation rather than by
 * import: it is a hand-written DOM string, and an element the card gains has
 * to appear in both files or every test in the other one throws.
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
    <p id="watch-status" role="status" aria-live="polite">Loading</p>
    <input id="watch-threshold" type="number" min="0.5" max="100" step="0.5" placeholder="5">
    <input id="watch-webhook" type="url">
    <div class="watch-actions">
      <button id="watch-toggle-btn">Watch this repository</button>
      <button id="watch-save-btn" hidden>Save changes</button>
      <button id="watch-unwatch-btn" hidden>Remove watch</button>
    </div>
  </div>
</body>`;

let n = 0;

async function load(
  responses,
  { run = { repo_url: 'https://github.com/x/watched' }, confirm = true } = {},
) {
  const dom = new JSDOM(CARD, { url: 'http://localhost/dashboard.html' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  globalThis.HTMLElement = dom.window.HTMLElement;
  globalThis.CustomEvent = dom.window.CustomEvent;

  const confirms = [];
  dom.window.confirm = (message) => {
    confirms.push(String(message));
    return confirm;
  };

  const calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, method: opts.method || 'GET', body: opts.body });
    const next = responses.shift() || { ok: true, body: { watched: [] } };
    return {
      ok: next.ok !== false,
      status: next.status || (next.ok === false ? 400 : 200),
      headers: { get: () => null },
      json: async () => {
        if (next.body === null) throw new SyntaxError('no body');
        return next.body;
      },
    };
  };

  // state.js without a cache-buster: watch.js resolves '../state.js' bare and
  // a query string is not inherited by a relative import, so busting it here
  // would hand this test a different store than the module reads.
  const state = await import(pathToFileURL(resolve(JS, 'state.js')).href);
  state.state.latestRun = run;
  const mod = await import(
    `${pathToFileURL(resolve(JS, 'render/watch.js')).href}?i6=${n++}`
  );
  return { mod, document: dom.window.document, calls, confirms };
}

/** A watch as the API returns one, with the fields the card now reads. */
function watched(over = {}) {
  return {
    id: 9,
    repo_url: 'https://github.com/x/watched',
    active: true,
    health_drop_threshold: 2.5,
    has_webhook: true,
    last_checked_at: '2026-08-25T10:00:00+00:00',
    last_status: 'No regression. Health 91.0.',
    ...over,
  };
}

const status = (d) => d.getElementById('watch-status').textContent;


describe('I-6: the card shows what is configured', () => {
  it('puts the saved threshold in the input', async () => {
    const { mod, document } = await load([{ body: { watched: [watched()] } }]);
    await mod.loadWatchState();

    assert.equal(
      document.getElementById('watch-threshold').value,
      '2.5',
      'the input showed the template default instead of the saved value',
    );
  });

  it('leaves the threshold empty when nothing is watched', async () => {
    // An empty box with a placeholder offers a default. A filled one claims a
    // saved setting that does not exist.
    const { mod, document } = await load([{ body: { watched: [] } }]);
    await mod.loadWatchState();

    assert.equal(document.getElementById('watch-threshold').value, '');
  });

  it('does not overwrite the threshold while it is being edited', async () => {
    // loadWatchState runs on every thirty-second poll.
    const { mod, document } = await load([
      { body: { watched: [watched()] } },
      { body: { watched: [watched()] } },
    ]);
    await mod.loadWatchState();

    const input = document.getElementById('watch-threshold');
    input.value = '7';
    input.focus();
    await mod.loadWatchState();

    assert.equal(input.value, '7', 'a poll deleted what the user was typing');
  });

  it('says whether a webhook is set, and never what it is', async () => {
    const { mod, document } = await load([{ body: { watched: [watched()] } }]);
    await mod.loadWatchState();

    assert.match(status(document), /sent to your webhook/i);
    assert.equal(document.getElementById('watch-webhook').value, '');
  });

  it('says when no webhook is set, rather than staying silent', async () => {
    const { mod, document } = await load([
      { body: { watched: [watched({ has_webhook: false })] } },
    ]);
    await mod.loadWatchState();

    assert.match(status(document), /no webhook is set/i);
  });

  it('reports a paused watch as paused, not as unwatched', async () => {
    const { mod, document } = await load([
      { body: { watched: [watched({ active: false })] } },
    ]);
    await mod.loadWatchState();

    assert.match(status(document), /^Paused\./);
    assert.match(document.getElementById('watch-toggle-btn').textContent, /resume/i);
  });

  it('says a repository has not been scanned yet rather than nothing', async () => {
    const { mod, document } = await load([
      { body: { watched: [watched({ last_checked_at: null, last_status: null })] } },
    ]);
    await mod.loadWatchState();

    assert.match(status(document), /not scanned yet/i);
  });

  it('offers Save and Remove only once there is a watch', async () => {
    const unwatchedCard = await load([{ body: { watched: [] } }]);
    await unwatchedCard.mod.loadWatchState();
    assert.equal(unwatchedCard.document.getElementById('watch-save-btn').hidden, true);
    assert.equal(unwatchedCard.document.getElementById('watch-unwatch-btn').hidden, true);

    const watchedCard = await load([{ body: { watched: [watched()] } }]);
    await watchedCard.mod.loadWatchState();
    assert.equal(watchedCard.document.getElementById('watch-save-btn').hidden, false);
    assert.equal(watchedCard.document.getElementById('watch-unwatch-btn').hidden, false);
  });

  it('claims nothing about when the next scan will run', async () => {
    // Nothing in the backend records one: the schedule is two worker constants
    // plus whether the worker is alive. A countdown here would be a guess
    // presented as a fact.
    const { mod, document } = await load([{ body: { watched: [watched()] } }]);
    await mod.loadWatchState();

    const text = status(document);
    assert.ok(!/next scan|due in|next check/i.test(text), text);
  });
});


describe('I-6: editing the threshold', () => {
  it('PATCHes the new value', async () => {
    const { mod, document, calls } = await load([
      { body: { watched: [watched()] } },
      { body: { watched: watched({ health_drop_threshold: 8 }) } },
    ]);
    await mod.loadWatchState();

    document.getElementById('watch-threshold').value = '8';
    await mod.saveWatchSettings();

    const patch = calls.find((c) => c.method === 'PATCH');
    assert.ok(patch, 'saving did not reach the server');
    assert.equal(patch.url, '/api/v1/watch/9');
    assert.equal(JSON.parse(patch.body).health_drop_threshold, 8);
  });

  it('reports the saved value only after the server confirms it', async () => {
    // The input holds what was typed; the card must report what was stored,
    // and until the response arrives those are different things.
    const { mod, document } = await load([
      { body: { watched: [watched()] } },
      { body: { watched: watched({ health_drop_threshold: 8 }) } },
    ]);
    await mod.loadWatchState();

    document.getElementById('watch-threshold').value = '8';
    await mod.saveWatchSettings();

    assert.match(status(document), /more than 8\./);
  });

  it('does not claim success when the save failed', async () => {
    const { mod, document } = await load([
      { body: { watched: [watched()] } },
      { ok: false, status: 500, body: {} },
    ]);
    await mod.loadWatchState();

    document.getElementById('watch-threshold').value = '8';
    await mod.saveWatchSettings();

    assert.match(status(document), /could not update/i);
    assert.ok(!/more than 8\./.test(status(document)), status(document));
  });

  it('refuses a threshold the server would reject, without asking it', async () => {
    // 0.1 against a field declared ge=0.5 comes back a 422 whose detail is a
    // list, which rendered as "Could not update: [object Object]".
    const { mod, document, calls } = await load([{ body: { watched: [watched()] } }]);
    await mod.loadWatchState();
    const before = calls.length;

    document.getElementById('watch-threshold').value = '0.1';
    await mod.saveWatchSettings();

    assert.equal(calls.length, before, 'an out-of-range threshold was sent anyway');
    assert.match(status(document), /between/i);
  });

  it('does not silently substitute a default for nonsense', async () => {
    // `parseFloat(x) || 5.0` turned an empty box, "abc" and 0 all into 5.
    const { mod, document, calls } = await load([{ body: { watched: [watched()] } }]);
    await mod.loadWatchState();
    const before = calls.length;

    document.getElementById('watch-threshold').value = '';
    await mod.saveWatchSettings();

    assert.equal(calls.length, before, 'a blank field was saved as 5');
    assert.ok(!/more than 5\./.test(status(document)), status(document));
  });

  it('reads a validation error from the server rather than printing an object', async () => {
    const { mod, document } = await load([
      { body: { watched: [watched()] } },
      {
        ok: false,
        status: 422,
        body: {
          detail: [
            {
              loc: ['body', 'health_drop_threshold'],
              msg: 'Input should be greater than or equal to 0.5',
            },
          ],
        },
      },
    ]);
    await mod.loadWatchState();

    document.getElementById('watch-threshold').value = '9';
    await mod.saveWatchSettings();

    assert.match(status(document), /greater than or equal to 0\.5/);
    assert.ok(!status(document).includes('[object Object]'), status(document));
  });

  it('does not send a webhook field when the box is empty', async () => {
    // The API reads an absent field as "leave unchanged". Sending null for an
    // untouched box is pointless at best and destructive if that ever changes.
    const { mod, document, calls } = await load([
      { body: { watched: [watched()] } },
      { body: { watched: watched() } },
    ]);
    await mod.loadWatchState();

    document.getElementById('watch-threshold').value = '3';
    await mod.saveWatchSettings();

    const patch = calls.find((c) => c.method === 'PATCH');
    assert.ok(!('webhook_url' in JSON.parse(patch.body)), patch.body);
  });
});


describe('I-6: removing a watch', () => {
  it('asks first, and does nothing when declined', async () => {
    const { mod, document, calls, confirms } = await load(
      [{ body: { watched: [watched()] } }],
      { confirm: false },
    );
    await mod.loadWatchState();
    const before = calls.length;

    await mod.unwatch();

    assert.equal(confirms.length, 1, 'the watch was deleted without asking');
    assert.match(confirms[0], /deleted/i);
    assert.equal(calls.length, before, 'declining still deleted it');
    assert.match(status(document), /^Watched\./);
  });

  it('DELETEs on the server and then reports it as not watched', async () => {
    const { mod, document, calls } = await load([
      { body: { watched: [watched()] } },
      { status: 204, body: null },
    ]);
    await mod.loadWatchState();

    await mod.unwatch();

    const del = calls.find((c) => c.method === 'DELETE');
    assert.ok(del, 'nothing was deleted on the server');
    assert.equal(del.url, '/api/v1/watch/9');
    assert.match(status(document), /not watched/i);
    assert.equal(document.getElementById('watch-unwatch-btn').hidden, true);
  });

  it('keeps showing the watch when the delete fails', async () => {
    // "Not watched" over a row still being scanned every night is exactly what
    // a client-only state change would produce.
    const { mod, document } = await load([
      { body: { watched: [watched()] } },
      { ok: false, status: 500, body: {} },
    ]);
    await mod.loadWatchState();

    await mod.unwatch();

    assert.match(status(document), /could not update/i);
    assert.equal(document.getElementById('watch-unwatch-btn').hidden, false);
  });
});


describe('I-6: pausing keeps the configuration', () => {
  it('pauses with a PATCH', async () => {
    const { mod, calls } = await load([
      { body: { watched: [watched()] } },
      { body: { watched: watched({ active: false }) } },
    ]);
    await mod.loadWatchState();
    await mod.toggleWatch();

    const patch = calls.find((c) => c.method === 'PATCH');
    assert.ok(patch, 'pausing did not PATCH');
    assert.equal(JSON.parse(patch.body).active, false);
    assert.ok(!calls.some((c) => c.method === 'DELETE'), 'pausing deleted the watch');
  });

  it('resumes with a PATCH, not the upsert that wipes the webhook', async () => {
    // POST /watch is an upsert: store.watch_repository overwrites webhook_url
    // and health_drop_threshold unconditionally. Resuming through it silently
    // cleared a configured webhook and reset the threshold to 5.
    const { mod, calls } = await load([
      { body: { watched: [watched({ active: false })] } },
      { body: { watched: watched({ active: true }) } },
    ]);
    await mod.loadWatchState();
    await mod.toggleWatch();

    assert.ok(!calls.some((c) => c.method === 'POST'), 'resuming went through the upsert');
    const body = JSON.parse(calls.find((c) => c.method === 'PATCH').body);
    assert.equal(body.active, true);
    assert.ok(!('webhook_url' in body), 'resuming would have overwritten the stored webhook');
    assert.ok(
      !('health_drop_threshold' in body),
      'resuming would have overwritten the stored threshold',
    );
  });
});


describe('I-6: the unknown state', () => {
  it('disables every control, including the inputs', async () => {
    const { mod, document } = await load([{ ok: false, status: 500, body: {} }]);
    await mod.loadWatchState();

    assert.equal(document.getElementById('watch-toggle-btn').disabled, true);
    assert.equal(document.getElementById('watch-threshold').disabled, true);
    assert.equal(document.getElementById('watch-webhook').disabled, true);
  });

  it('does not claim the repository is unwatched', async () => {
    const { mod, document } = await load([{ ok: false, status: 500, body: {} }]);
    await mod.loadWatchState();

    assert.match(status(document), /couldn't check/i);
    assert.ok(!/not watched/i.test(status(document)), status(document));
  });

  it('refuses to act on a state it does not know', async () => {
    const { mod, calls } = await load([{ ok: false, status: 500, body: {} }]);
    await mod.loadWatchState();
    const before = calls.length;

    await mod.toggleWatch();
    await mod.saveWatchSettings();
    await mod.unwatch();

    assert.equal(calls.length, before, 'a control acted while the state was unknown');
  });

  it('recovers once the next poll succeeds', async () => {
    const { mod, document } = await load([
      { ok: false, status: 500, body: {} },
      { body: { watched: [watched()] } },
    ]);
    await mod.loadWatchState();
    assert.equal(document.getElementById('watch-toggle-btn').disabled, true);

    await mod.loadWatchState();

    assert.equal(document.getElementById('watch-toggle-btn').disabled, false);
    assert.match(status(document), /^Watched\./);
  });
});
