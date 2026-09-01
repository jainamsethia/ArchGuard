/**
 * What the dashboard says when a request does not come back.
 *
 * `safeFetch` caught everything, replaced the skeletons with `--` and put the
 * reason in a `title` attribute. Five materially different situations arrived
 * on screen looking identical: a signed-out session, a rate limit, a server
 * fault, a dropped connection, and a repository that genuinely has no analyses
 * yet. The last of those is the only one where "no data" is the truth.
 *
 * A `title` is not an answer either. It needs a mouse to read, screen readers
 * treat it inconsistently, and it disappears the moment the pointer moves.
 *
 * So the fetch layer classifies the failure and the dashboard renders it. The
 * classification is what these tests are about: the categories have to be
 * distinguishable from each other and from an empty result, because everything
 * downstream keys off them.
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

let n = 0;

/**
 * Load api.js against a fresh DOM with `fetch` answering as told.
 *
 * A cache-busting query per load because ES modules are cached by URL and
 * api.js keeps module-level state -- the recorded failure -- that must not
 * leak from one test into the next.
 */
async function load(responder) {
  const dom = new JSDOM('<!doctype html><body><span class="skeleton">x</span></body>', {
    url: 'http://localhost/dashboard.html?job_id=abc',
  });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  globalThis.CustomEvent = dom.window.CustomEvent;

  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(String(url));
    return responder(String(url), init);
  };
  dom.window.fetch = globalThis.fetch;

  const api = await import(`${pathToFileURL(resolve(JS, 'api.js')).href}?i=${n++}`);
  return { api, dom, calls, window: dom.window };
}

/** A Response-alike. jsdom has no fetch, so nothing else supplies one. */
function reply(status, body, headers = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => headers[k] ?? headers[k.toLowerCase()] ?? null },
    json: async () => {
      if (body === '__invalid__') throw new SyntaxError('Unexpected token');
      return body;
    },
  };
}

describe('I-5: classifying an API failure', () => {
  it('returns the data on 200', async () => {
    const { api } = await load(() => reply(200, { runs: [{ id: 1 }] }));

    const data = await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.deepEqual(data, { runs: [{ id: 1 }] });
    assert.equal(api.lastApiFailure(), null, 'a success recorded a failure');
  });

  it('treats a legitimately empty response as data, not as a failure', async () => {
    // The one case where "nothing to show" is the truth. It has to stay
    // distinguishable from the four below or the dashboard cannot tell a new
    // account from a broken one.
    const { api } = await load(() => reply(200, { runs: [] }));

    const data = await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.deepEqual(data, { runs: [] });
    assert.equal(api.lastApiFailure(), null);
  });

  it('classifies 401 as an authentication failure', async () => {
    const { api } = await load(() => reply(401, { detail: 'Not authenticated' }));

    const data = await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.deepEqual(data, { runs: [] }, 'the caller still gets its fallback');
    assert.equal(api.lastApiFailure().kind, api.FAILURE.AUTH);
  });

  it('classifies 403 as an authentication failure too', async () => {
    const { api } = await load(() => reply(403, {}));

    await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.equal(api.lastApiFailure().kind, api.FAILURE.AUTH);
  });

  it('classifies 429 as a rate limit', async () => {
    const { api } = await load(() => reply(429, { detail: 'Too many requests' }));

    await api.safeFetch('/api/v1/runs', { runs: [] });

    const failure = api.lastApiFailure();
    assert.equal(failure.kind, api.FAILURE.RATE_LIMIT);
    assert.equal(failure.retryAfter, null, 'no Retry-After means no invented number');
  });

  it('reads Retry-After when the server sends one', async () => {
    const { api } = await load(() =>
      reply(429, { detail: 'Too many requests' }, { 'Retry-After': '45' }),
    );

    await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.equal(api.lastApiFailure().retryAfter, 45);
  });

  it('ignores a nonsense Retry-After rather than scheduling on it', async () => {
    const { api } = await load(() =>
      reply(429, {}, { 'Retry-After': 'Wed, 21 Oct 2015 07:28:00 GMT' }),
    );

    await api.safeFetch('/api/v1/runs', { runs: [] });

    // The HTTP-date form is legal and we do not parse it; guessing would be
    // worse than falling back to a default the caller controls.
    assert.equal(api.lastApiFailure().retryAfter, null);
  });

  it('classifies 5xx as a server failure', async () => {
    const { api } = await load(() => reply(500, { detail: 'boom' }));

    await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.equal(api.lastApiFailure().kind, api.FAILURE.SERVER);
  });

  it('classifies a dropped connection as a network failure', async () => {
    const { api } = await load(() => {
      throw new TypeError('Failed to fetch');
    });

    await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.equal(api.lastApiFailure().kind, api.FAILURE.NETWORK);
  });

  it('classifies a malformed body as a data failure, not as success', async () => {
    // A 200 whose body is not JSON is a broken response, and returning the
    // fallback silently would report it as an empty repository.
    const { api } = await load(() => reply(200, '__invalid__'));

    const data = await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.deepEqual(data, { runs: [] });
    assert.equal(api.lastApiFailure().kind, api.FAILURE.DATA);
  });
});

describe('I-5: what reaches the user', () => {
  it('does not put the reason only in a title attribute', async () => {
    // The old behaviour. A title needs a pointer to read, is announced
    // inconsistently, and vanishes when the pointer moves.
    const { api, window } = await load(() => reply(500, {}));

    await api.safeFetch('/api/v1/runs', { runs: [] });

    const skeleton = window.document.querySelector('span');
    assert.ok(
      !skeleton.title || !/failed/i.test(skeleton.title),
      'the failure is still being reported through a title attribute',
    );
  });

  it('carries no server text into the classification', async () => {
    // Backend exception strings carry paths, module names and query fragments.
    // The category is what the UI needs; the detail belongs in the console.
    const { api } = await load(() =>
      reply(500, { detail: 'psycopg2.errors: relation "runs" does not exist at /srv/app' }),
    );

    await api.safeFetch('/api/v1/runs', { runs: [] });

    const serialised = JSON.stringify(api.lastApiFailure());
    assert.ok(!serialised.includes('psycopg2'), serialised);
    assert.ok(!serialised.includes('/srv/app'), serialised);
  });

  it('announces an authentication failure so the page can react', async () => {
    const { api, window } = await load(() => reply(401, {}));

    const heard = [];
    window.addEventListener('archguard:apifailure', (e) => heard.push(e.detail.kind));

    await api.safeFetch('/api/v1/runs', { runs: [] });

    assert.deepEqual(heard, [api.FAILURE.AUTH]);
  });
});

describe('I-5: which failure wins', () => {
  it('reports authentication ahead of anything else in the same cycle', async () => {
    // Five requests go out together. If one is 401 the session is gone, and
    // that is the only thing worth telling the user -- the 500 behind it is a
    // consequence of asking again with no credentials.
    const { api } = await load((url) =>
      url.includes('modules') ? reply(401, {}) : reply(500, {}),
    );

    await Promise.all([
      api.safeFetch('/api/v1/runs', { runs: [] }),
      api.safeFetch('/api/v1/modules', { modules: [] }),
      api.safeFetch('/api/v1/evolution/trends', { trends: [] }),
    ]);

    assert.equal(api.lastApiFailure().kind, api.FAILURE.AUTH);
  });

  it('reports a rate limit ahead of a server error', async () => {
    const { api } = await load((url) =>
      url.includes('modules') ? reply(429, {}) : reply(500, {}),
    );

    await Promise.all([
      api.safeFetch('/api/v1/runs', { runs: [] }),
      api.safeFetch('/api/v1/modules', { modules: [] }),
    ]);

    assert.equal(api.lastApiFailure().kind, api.FAILURE.RATE_LIMIT);
  });

  it('forgets the previous cycle when a new one starts', async () => {
    // Without this a single blip would leave the dashboard reporting a failure
    // for the rest of the session.
    const { api } = await load(() => reply(500, {}));
    await api.safeFetch('/api/v1/runs', { runs: [] });
    assert.ok(api.lastApiFailure());

    api.beginFetchCycle();

    assert.equal(api.lastApiFailure(), null);
  });
});
