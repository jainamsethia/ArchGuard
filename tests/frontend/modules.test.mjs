/**
 * The separated modules, imported directly.
 *
 * These could not have been written before. `dashboard.js` was one 2,174-line
 * script scope: reaching any function meant evaluating the whole file against
 * a full page, so every test was an integration test whether it wanted to be
 * or not, and a helper like `sanitize` could only be exercised through
 * whatever rendered with it.
 *
 * Each module here is imported on its own, given only the DOM it actually
 * needs, and asserted directly -- which is the point of the split, rather than
 * the file count.
 */

import { strict as assert } from 'node:assert';
import { describe, it, beforeEach } from 'node:test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { JSDOM } from 'jsdom';

const JS = resolve(dirname(fileURLToPath(import.meta.url)), '../../archguard/dashboard/static/js');

/** Import a module with a DOM in place, isolated from other tests. */
let n = 0;
async function load(rel, html = '<!doctype html><body></body>') {
  const dom = new JSDOM(html, { url: 'http://localhost/dashboard.html' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  // switchTab writes the active tab into the URL fragment so a shared link
  // opens where the sender was looking.
  globalThis.history = dom.window.history;
  const mod = await import(`${pathToFileURL(resolve(JS, rel)).href}?m=${n++}`);
  return { mod, dom, document: dom.window.document };
}

// --------------------------------------------------------------- state.js

describe('state', () => {
  it('holds what six window globals used to', async () => {
    const { mod } = await load('state.js');
    const keys = Object.keys(mod.state).sort();
    assert.deepEqual(keys, [
      'currentViolationsPage',
      'latestRun',
      'recentRuns',
      'remediationSelectedKeys',
      'violationsPerPage',
      'visNetwork',
    ]);
  });

  it('starts on the first page of violations', async () => {
    const { mod } = await load('state.js');
    assert.equal(mod.state.currentViolationsPage, 1);
    assert.equal(mod.state.violationsPerPage, 20);
  });

  it('can be reset, so one test does not leak into the next', async () => {
    const { mod } = await load('state.js');
    mod.state.latestRun = { score: 1 };
    mod.state.currentViolationsPage = 7;
    mod.state.remediationSelectedKeys.add('x');

    mod.resetState();

    assert.equal(mod.state.latestRun, null);
    assert.equal(mod.state.currentViolationsPage, 1);
    assert.equal(mod.state.remediationSelectedKeys.size, 0);
  });

  it('is not published on window', async () => {
    // The whole point: these were readable and writable by any script on the
    // page, including an extension.
    const { mod, dom } = await load('state.js');
    assert.ok(mod.state, 'the store should exist as an export');
    for (const old of ['latestRun', 'currentViolationsPage', 'remediationSelectedKeys']) {
      assert.equal(dom.window[old], undefined, `window.${old} is still there`);
    }
  });
});

// ----------------------------------------------------------------- dom.js

describe('dom helpers', () => {
  let sanitize, getSeverityClass, getEmptyStateHtml, renderMarkdown;

  beforeEach(async () => {
    ({ mod: { sanitize, getSeverityClass, getEmptyStateHtml, renderMarkdown } } =
      await load('dom.js'));
  });

  it('escapes the characters that end an attribute or open a tag', () => {
    const out = sanitize(`<img src=x onerror="alert(1)">`);
    assert.ok(!out.includes('<img'), out);
    assert.ok(!out.includes('"'), out);
  });

  it('escapes ampersands first, so an entity cannot be smuggled in', () => {
    // &lt;script&gt; must not decode back into a tag.
    assert.equal(sanitize('&lt;'), '&amp;lt;');
  });

  it('survives a null violation field', () => {
    // Violations come from an untrusted repository; fields go missing.
    assert.doesNotThrow(() => sanitize(null));
    assert.doesNotThrow(() => sanitize(undefined));
  });

  it('maps severities to their badge class', () => {
    assert.match(getSeverityClass('critical'), /critical/);
    assert.match(getSeverityClass('high'), /high/);
  });

  it('gives an unknown severity a class rather than undefined', () => {
    const out = getSeverityClass('banana');
    assert.equal(typeof out, 'string');
    assert.ok(out.length > 0);
  });

  it('builds an empty state that says what to do next', () => {
    const html = getEmptyStateHtml('icon', 'Nothing here', 'Try running a scan');
    assert.ok(html.includes('Nothing here'));
    assert.ok(html.includes('Try running a scan'));
  });

  it('renders markdown without passing raw html through', () => {
    const out = renderMarkdown('# Title\n\n<script>alert(1)</script>');
    assert.ok(!out.includes('<script>'), out.slice(0, 120));
  });
});

// ------------------------------------------------------------- api.js

describe('api', () => {
  it('derives both query forms from the job in the URL', async () => {
    const dom = new JSDOM('<!doctype html><body></body>', {
      url: 'http://localhost/dashboard.html?job_id=abc123',
    });
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    const mod = await import(`${pathToFileURL(resolve(JS, 'api.js')).href}?m=${n++}`);

    assert.equal(mod.highlightJobId, 'abc123');
    assert.equal(mod.jobQuery, '?job_id=abc123');
    assert.equal(mod.jobQueryAmp, '&job_id=abc123');
  });

  it('yields empty suffixes when the page was opened without a job', async () => {
    const { mod } = await load('api.js');
    assert.equal(mod.highlightJobId, null);
    assert.equal(mod.jobQuery, '');
    assert.equal(mod.jobQueryAmp, '');
  });

  it('returns the fallback rather than throwing when a fetch fails', async () => {
    const { mod } = await load('api.js');
    globalThis.fetch = () => Promise.reject(new Error('offline'));
    const out = await mod.safeFetch('/api/v1/runs', { runs: [] });
    assert.deepEqual(out, { runs: [] });
  });

  it('returns the fallback on a non-ok response', async () => {
    // A 500 on one panel must not blank the other four.
    const { mod } = await load('api.js');
    globalThis.fetch = () =>
      Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
    assert.deepEqual(await mod.safeFetch('/x', { fallback: true }), { fallback: true });
  });
});

// ------------------------------------------------------------- ui/tabs.js

describe('tab activation registry', () => {
  it('lets main say what a tab loads without tabs importing it', async () => {
    // tabs.js used to call initDependencyGraph() and loadSuppressions()
    // directly, so the tab chrome depended on two feature modules that both
    // depend on it back. Registration is what breaks that.
    const { mod } = await load('ui/tabs.js');
    let called = 0;
    mod.onTabActivated('dependencies', () => { called += 1; });

    mod.switchTab('dependencies');
    assert.equal(called, 1);
  });

  it('does nothing for a tab nobody registered', async () => {
    const { mod } = await load('ui/tabs.js');
    assert.doesNotThrow(() => mod.switchTab('suppressions'));
  });

  it('imports no feature module', async () => {
    // The structural half of the same point: if this file grows an import of
    // a render module, the cycle is back whatever the registry says.
    const { readFileSync } = await import('node:fs');
    const src = readFileSync(resolve(JS, 'ui/tabs.js'), 'utf8');
    const imports = [...src.matchAll(/from '([^']+)'/g)].map((m) => m[1]);
    const features = imports.filter((i) => i.includes('render/') || i.includes('features/'));
    assert.deepEqual(features, [], `tabs.js imports ${features.join(', ')}`);
  });
});

// -------------------------------------------------- render/violations.js

describe('violations rendering', () => {
  it('keys a violation by the fields the compare view diffs on', async () => {
    const { mod } = await load('render/violations.js');
    const key = mod.violationKey({ module: 'api', layer: 1, message: 'bad import' });
    assert.ok(key.includes('api'));
    assert.ok(key.includes('bad import'));
  });

  it('gives two different violations two different keys', async () => {
    const { mod } = await load('render/violations.js');
    const a = mod.violationKey({ module: 'api', layer: 1, message: 'x' });
    const b = mod.violationKey({ module: 'api', layer: 2, message: 'x' });
    assert.notEqual(a, b);
  });

  it('keys a violation with missing fields without throwing', async () => {
    const { mod } = await load('render/violations.js');
    assert.doesNotThrow(() => mod.violationKey({}));
  });
});

// ----------------------------------------------------------- module graph

describe('module graph', () => {
  it('has no import cycles', async () => {
    const { readdirSync, statSync, readFileSync } = await import('node:fs');
    const { join, dirname: dn, resolve: rs } = await import('node:path');

    const walk = (d) =>
      readdirSync(d).flatMap((f) => {
        const p = join(d, f);
        return statSync(p).isDirectory() ? walk(p) : p.endsWith('.js') ? [p] : [];
      });
    const files = walk(JS);
    const graph = new Map(
      files.map((f) => [
        f,
        [...readFileSync(f, 'utf8').matchAll(/from '([^']+)'/g)].map((m) => rs(dn(f), m[1])),
      ]),
    );

    const cycles = [];
    const done = new Set();
    const stack = new Set();
    const visit = (nd, path) => {
      if (stack.has(nd)) return cycles.push([...path, nd]);
      if (done.has(nd)) return;
      done.add(nd);
      stack.add(nd);
      for (const dep of graph.get(nd) ?? []) visit(dep, [...path, nd]);
      stack.delete(nd);
    };
    for (const f of files) visit(f, []);

    assert.deepEqual(cycles, [], 'import cycle in the dashboard modules');
  });

  it('keeps every module small enough to read', async () => {
    // The file this replaced was 2,174 lines. A module creeping back toward
    // that is the thing to notice early.
    const { readdirSync, statSync, readFileSync } = await import('node:fs');
    const { join } = await import('node:path');
    const walk = (d) =>
      readdirSync(d).flatMap((f) => {
        const p = join(d, f);
        return statSync(p).isDirectory() ? walk(p) : p.endsWith('.js') ? [p] : [];
      });
    const oversized = walk(JS)
      .map((f) => [f, readFileSync(f, 'utf8').split('\n').length])
      .filter(([, lines]) => lines > 400);
    assert.deepEqual(oversized.map(([f, l]) => `${f}: ${l} lines`), []);
  });
});
