/**
 * Loads the real dashboard page into jsdom so its behaviour can be asserted.
 *
 * dashboard.js is a plain script, not a module: it defines its functions on the
 * global scope and calls fetchData() at the bottom. So the harness builds a
 * window from the real template, installs the globals the page expects a
 * browser and two vendored libraries to provide, and evaluates the file in that
 * context. Everything it defines is then reachable on `window`.
 *
 * Nothing here stubs the code under test -- only the browser around it.
 */

import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM, VirtualConsole } from 'jsdom';

const HERE = dirname(fileURLToPath(import.meta.url));
const DASHBOARD = resolve(HERE, '../../archguard/dashboard');

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

  const source = readFileSync(resolve(DASHBOARD, 'static/dashboard.js'), 'utf8');
  window.eval(source);

  // Let the page's own DOMContentLoaded handlers and the initial fetchData()
  // settle before anything is asserted.
  window.document.dispatchEvent(
    new window.Event('DOMContentLoaded', { bubbles: true }),
  );
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));

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
