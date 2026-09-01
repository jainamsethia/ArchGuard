/**
 * What the dashboard *shows* when a refresh fails.
 *
 * The classification lives in api.js and is covered in api_failures.test.mjs.
 * This is the half that decides what a person sees, and it turns on one
 * ordering: a failed request returns its fallback -- an empty list, a null --
 * which the polling loop then could not tell from a repository that genuinely
 * has no analyses. So a signed-out session rendered "No analyses yet. Analyze a
 * repository." to somebody whose analyses were sitting there behind a 401.
 *
 * Four outcomes have to stay distinguishable:
 *
 *   empty        -> the empty-state panel, unchanged
 *   401          -> the sign-in overlay, which the page already has
 *   429          -> a rate-limit banner, and stop asking
 *   5xx/network  -> an error banner with a way to retry
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

/** The parts of the dashboard the polling loop writes into. */
const PAGE = `<!doctype html><body>
  <div class="dashboard-container" id="dashboard-main">
    <div id="overview"></div>
    <span id="refresh-loader"></span>
    <span id="last-updated"></span>
    <span class="metric-value skeleton" id="health-score">--</span>
    <div id="layer-status" hidden><p id="layer-status-intro"></p><ul id="layer-status-list"></ul></div>
    <div id="violations-table-container"></div>
  </div>
</body>`;

let n = 0;

async function load(responder) {
  const dom = new JSDOM(PAGE, { url: 'http://localhost/dashboard.html' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  globalThis.CustomEvent = dom.window.CustomEvent;
  globalThis.HTMLElement = dom.window.HTMLElement;
  // Node's own timers, deliberately. Re-pointing the globals at jsdom's
  // recurses through its own timer-initialisation steps and blows the stack,
  // and the module under test only needs *a* working setTimeout.

  const calls = [];
  globalThis.fetch = async (url) => {
    calls.push(String(url));
    return responder(String(url));
  };
  dom.window.fetch = globalThis.fetch;

  const suffix = `?i=${n++}`;
  const poll = await import(`${pathToFileURL(resolve(JS, 'poll.js')).href}${suffix}`);
  return { poll, dom, calls, window: dom.window, document: dom.window.document };
}

function reply(status, body, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => headers[k] ?? headers[k.toLowerCase()] ?? null },
    json: async () => body,
  };
}

/**
 * A genuinely empty account, answered the way the API answers it.
 *
 * Endpoint-aware on purpose: `/runs/latest` returns null when there is no run,
 * and the polling loop's empty check reads both that and the runs list. A
 * responder that returned `{runs: []}` for every URL would leave `latestData`
 * truthy and never reach the branch under test.
 */
const EMPTY_OK = (url) =>
  url.includes('/runs/latest') || url.includes('/evolution/latest')
    ? reply(200, null)
    : reply(200, { runs: [], modules: [], trends: [] });
const banner = (document) => document.getElementById('api-status');


describe('I-5: a failure is not an empty repository', () => {
  it('renders the empty state when the API really says there is nothing', async () => {
    const { poll, document } = await load(EMPTY_OK);

    await poll.fetchData();

    assert.ok(
      document.getElementById('empty-state-panel'),
      'a genuinely empty account no longer gets the empty state',
    );
    assert.ok(!banner(document) || banner(document).hidden, 'an empty account was shown an error');
  });

  it('does not render the empty state when the requests failed', async () => {
    // The defect. Fallbacks are empty by construction, so "no runs" was true
    // of a 500 and of a new account alike.
    const { poll, document } = await load(() => reply(500, {}));

    await poll.fetchData();

    assert.equal(
      document.getElementById('empty-state-panel'),
      null,
      'a server failure was reported as "No analyses yet"',
    );
  });

  it('says what went wrong, in the page rather than in a title', async () => {
    const { poll, document } = await load(() => reply(500, {}));

    await poll.fetchData();

    const el = banner(document);
    assert.ok(el && !el.hidden, 'no failure was shown at all');
    assert.match(el.textContent, /couldn't load/i);
  });

  it('marks the values underneath as not current', async () => {
    // They stay on screen -- last-known numbers are useful and blanking the
    // page on one failed refresh is its own kind of lie -- but they must not
    // read as this minute's measurement.
    const { poll, document } = await load(() => reply(500, {}));

    await poll.fetchData();

    assert.equal(document.getElementById('dashboard-main').getAttribute('data-stale'), 'true');
  });

  it('clears the failure once a refresh succeeds', async () => {
    let broken = true;
    const { poll, document } = await load(() => (broken ? reply(500, {}) : reply(200, { runs: [] })));

    await poll.fetchData();
    assert.ok(!banner(document).hidden);

    broken = false;
    await poll.fetchData();

    assert.ok(banner(document).hidden, 'the banner outlived the failure');
    assert.equal(document.getElementById('dashboard-main').getAttribute('data-stale'), null);
  });
});


describe('I-5: an expired session', () => {
  it('does not show a banner of its own', async () => {
    // The page already has an authentication UI. A second one, over a
    // dashboard the visitor can no longer read, would be worse than the `--`.
    const { poll, document } = await load(() => reply(401, {}));

    await poll.fetchData();

    assert.ok(!banner(document) || banner(document).hidden);
  });

  it('announces itself so the sign-in overlay can respond', async () => {
    const { poll, window } = await load(() => reply(401, {}));

    const heard = [];
    window.addEventListener('archguard:apifailure', (e) => heard.push(e.detail.kind));

    await poll.fetchData();

    assert.ok(heard.includes('auth'), `no auth failure announced: ${heard}`);
  });

  it('does not report the account as empty', async () => {
    const { poll, document } = await load(() => reply(401, {}));

    await poll.fetchData();

    assert.equal(document.getElementById('empty-state-panel'), null);
  });

  it('stops polling, because every further request is another 401', async () => {
    const { poll, calls } = await load(() => reply(401, {}));

    poll.startPolling();
    await poll.fetchData();
    const after = calls.length;

    // Nothing further should be scheduled; the visitor has to sign in first.
    await new Promise((r) => setTimeout(r, 30));
    assert.equal(calls.length, after, 'the dashboard kept polling a dead session');
  });
});


describe('I-5: a rate limit', () => {
  it('says it is a rate limit, not a server error', async () => {
    const { poll, document } = await load(() => reply(429, {}));

    await poll.fetchData();

    assert.match(banner(document).textContent, /too many requests/i);
  });

  it('quotes the wait when the server sends Retry-After', async () => {
    const { poll, document } = await load(() => reply(429, {}, { 'Retry-After': '45' }));

    await poll.fetchData();

    assert.match(banner(document).textContent, /45 seconds/);
  });

  it('does not retry immediately', async () => {
    // Polling straight back into a 429 is what spent the budget. The window
    // has to pass, and the dashboard has to stop asking while it does.
    const { poll, calls } = await load(() => reply(429, {}, { 'Retry-After': '30' }));

    poll.startPolling();
    await poll.fetchData();
    const after = calls.length;

    await new Promise((r) => setTimeout(r, 50));

    assert.equal(calls.length, after, 'the dashboard retried into its own rate limit');
  });

  it('offers a way to try again by keyboard', async () => {
    const { poll, document } = await load(() => reply(429, {}));

    await poll.fetchData();

    const retry = document.getElementById('api-status-retry');
    assert.ok(retry, 'no retry control');
    assert.equal(retry.tagName, 'BUTTON', 'a non-button retry is not keyboard-operable');
  });
});


describe('I-5: the failure region is announced', () => {
  it('is a polite live region rather than a silent div', async () => {
    const { poll, document } = await load(() => reply(500, {}));

    await poll.fetchData();

    const el = banner(document);
    assert.equal(el.getAttribute('role'), 'status');
    assert.equal(el.getAttribute('aria-live'), 'polite');
  });

  it('is out of the accessibility tree when nothing is wrong', async () => {
    const { poll, document } = await load(EMPTY_OK);

    await poll.fetchData();

    const el = banner(document);
    assert.ok(!el || el.hidden, 'an empty banner was left in the tree');
  });
});


describe('I-5: a network failure', () => {
  it('is reported as unreachable rather than as no data', async () => {
    const { poll, document } = await load(() => {
      throw new TypeError('Failed to fetch');
    });

    await poll.fetchData();

    assert.equal(document.getElementById('empty-state-panel'), null);
    assert.match(banner(document).textContent, /couldn't reach/i);
  });
});
