/**
 * Loads the real dashboard page into jsdom so its behaviour can be asserted.
 *
 * The dashboard is a graph of ES modules now, entered at js/main.js. jsdom does
 * not execute `<script type="module">`, so the harness builds the window from
 * the real template, installs the globals a browser and two vendored libraries
 * would provide, publishes them on globalThis, and imports the entry module --
 * which binds to those globals exactly as it would in a page.
 *
 * The import is cache-busted per load. Module state is per-URL and would
 * otherwise persist across tests, so the second test in a file would inherit
 * the first one's chart handles, polling flags and store.
 *
 * Nothing here stubs the code under test -- only the browser around it.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { JSDOM, VirtualConsole } from 'jsdom';

const HERE = dirname(fileURLToPath(import.meta.url));
const DASHBOARD = resolve(HERE, '../../archguard/dashboard');

/** Globals the page modules read as free identifiers. */
const GLOBALS = [
  'document',
  'fetch',
  // setInterval/clearInterval are the harness's own stubs below, so hoisting
  // them is safe. setTimeout is NOT: jsdom implements window.setTimeout in
  // terms of the global one, so pointing the global back at it recurses until
  // the stack dies. Node's own timers serve the modules fine.
  'setInterval',
  'clearInterval',
  'Chart',
  'vis',
  'location',
  'history',
  'Event',
  'URL',
  'Blob',
  'IntersectionObserver',
  'HTMLElement',
  'Node',
  'getComputedStyle',
];

/** Distinguishes each load's module registry. See the note at the top. */
let loadCounter = 0;

/** Set by loadDashboard; imports a module from the current load. */
let moduleFor = null;

/** Minimal stand-ins for Chart.js and vis-network, which are vendored blobs. */
function installVendorStubs(window) {
  const chartInstances = [];
  class Chart {
    constructor(ctx, config) {
      this.ctx = ctx;
      this.data = config.data;
      this.options = config.options;
      chartInstances.push(this);
    }
    update() {
      this.updated = (this.updated || 0) + 1;
    }
    destroy() {}
  }
  Chart.defaults = { color: '', borderColor: '', font: { family: '' } };
  window.Chart = Chart;
  window.__charts = chartInstances;

  // jsdom has no canvas implementation without the native `canvas` package,
  // and Chart.js asks every canvas for a 2d context before drawing.
  window.HTMLCanvasElement.prototype.getContext = () => ({
    createLinearGradient: () => ({ addColorStop() {} }),
    canvas: {},
  });

  window.vis = {
    DataSet: class {
      constructor(items) {
        this.items = items;
      }
    },
    Network: class {
      constructor(container, data) {
        this.container = container;
        this.data = data;
      }
      redraw() {}
      fit() {}
    },
  };
}

/**
 * @param {object} options
 * @param {(url: string) => any} options.respond  maps a request URL to a JSON body
 * @param {string} [options.search]               window.location.search
 */
export async function loadDashboard({ respond = () => ({}), search = '' } = {}) {
  const html = readFileSync(resolve(DASHBOARD, 'templates/dashboard.html'), 'utf8')
    // The template is served through Jinja; the nonce is irrelevant here.
    .replace(/\{\{\s*csp_nonce\s*\}\}/g, 'test-nonce')
    // Resolve the asset() helper the same way the app does, minus the content
    // fingerprint. Without this the strip below stops matching and quietly
    // does nothing, which is exactly what happened when fingerprinting landed.
    .replace(/\{\{\s*asset\('([^']+)'\)\s*\}\}/g, '/$1')
    // Vendored <script src> tags would 404; the stubs below replace them.
    .replace(/<script src="\/(vendor|theme|auth)[^"]*"><\/script>/g, '');

  const virtualConsole = new VirtualConsole();
  const consoleErrors = [];
  virtualConsole.on('jsdomError', (e) => consoleErrors.push(e));

  const dom = new JSDOM(html, {
    url: `http://localhost/dashboard.html${search}`,
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    virtualConsole,
  });
  const { window } = dom;

  installVendorStubs(window);

  const requests = [];
  window.fetch = (url, init) => {
    requests.push({ url: String(url), init });
    const body = respond(String(url));
    return Promise.resolve({
      ok: body !== undefined && body !== null,
      status: body === undefined || body === null ? 404 : 200,
      headers: { get: () => null },
      json: () => Promise.resolve(body ?? {}),
      text: () => Promise.resolve(JSON.stringify(body ?? {})),
    });
  };

  // The page polls on a timer and observes a sticky sentinel. Neither is under
  // test, and both would keep the process alive.
  const intervals = [];
  window.setInterval = (fn, ms) => {
    intervals.push({ fn, ms });
    return intervals.length;
  };
  window.clearInterval = (id) => {
    intervals[id - 1] = null;
  };
  window.IntersectionObserver = class {
    observe() {}
    disconnect() {}
  };
  window.__intervals = intervals;
  window.__requests = requests;
  window.__consoleErrors = consoleErrors;

  // The modules reference `document`, `window`, `fetch`, `Chart` and `vis` as
  // free identifiers, which resolve to globalThis under Node. Publishing the
  // jsdom window's versions there is what makes an unmodified page module run
  // outside a browser.
  // Installed for the lifetime of the test, not just for the import. The page
  // reads these when its handlers run, not when the module is evaluated, so
  // restoring them afterwards left every later call looking at an undefined
  // `document`. Each load overwrites them with its own window; node:test runs
  // files sequentially, so there is nothing to interleave.
  for (const key of GLOBALS) {
    globalThis[key] = window[key];
  }
  globalThis.window = window;

  {
    const load = loadCounter++;
    // Imports a page module sharing this load's registry, so a test reaches
    // the same instances main.js wired -- the same store, the same polling
    // handle, the same in-flight loader promise. The cache-buster has to match
    // exactly, or the test gets a second, unrelated copy of the module.
    moduleFor = (rel) =>
      import(`${pathToFileURL(resolve(DASHBOARD, 'static/js', rel)).href}?load=${load}`);

    const mod = await moduleFor('main.js');
    window.__module = mod;
    // Re-export what the page put on globalThis, so tests can drive the page
    // through the same names they used when it was one script.
    for (const [name, value] of Object.entries(mod)) {
      if (typeof value === 'function') window[name] = value;
    }
    window.__state = (await moduleFor('state.js')).state;
  }

  // Let the page's own DOMContentLoaded handlers and the initial fetchData()
  // settle before anything is asserted.
  window.document.dispatchEvent(
    new window.Event('DOMContentLoaded', { bubbles: true }),
  );
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

  // The window itself, with the module accessor hung off it. Callers written
  // against the old harness do `const window = await loadDashboard()`; newer
  // ones destructure `{ window, module }`. jsdom's window.window is itself, so
  // both read correctly from one return value.
  window.module = moduleFor;
  return window;
}

/** A minimal but well-formed persisted run, as /api/v1/runs/latest returns it. */
export function run(overrides = {}) {
  return {
    job_id: 'job-1',
    timestamp: '2026-08-20T10:00:00Z',
    score: 87.5,
    band: 'PASS',
    repo_url: 'https://github.com/pallets/flask',
    violations: [],
    layer_results: [],
    module_scores: {},
    metrics: {},
    ...overrides,
  };
}
