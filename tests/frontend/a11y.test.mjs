/**
 * Keyboard and screen-reader behaviour, asserted directly against the modules.
 *
 * The CSS has carried real accessibility intent from the start --
 * prefers-reduced-motion, prefers-contrast, :focus-visible, a light theme with
 * measured contrast. The JavaScript did not. Before this file:
 *
 *   - the modal set `aria-modal="true"` and then let Tab walk straight out of
 *     it into the page behind, which is the one thing aria-modal promises it
 *     will not do;
 *   - closing a modal dropped focus on <body>, so a keyboard user was returned
 *     to the top of the document rather than to the control they pressed;
 *   - the tablist had no arrow-key handling at all, and all four tabs sat in
 *     the tab order, which is neither of the two patterns ARIA allows;
 *   - tabpanels were unlabelled, so a screen reader announced "tab panel" with
 *     no indication of which one.
 *
 * Driven through jsdom rather than a browser because focus order and key
 * handling are exactly what jsdom models well, and because a unit test can
 * assert the wrap-around cases a click-through never reaches.
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

/** A DOM with the page globals the modules read, plus a fresh module registry. */
async function load(rel, html = '<!doctype html><body></body>') {
  const dom = new JSDOM(html, { url: 'http://localhost/dashboard.html' });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  globalThis.history = dom.window.history;
  globalThis.HTMLElement = dom.window.HTMLElement;
  // initTabChrome measures the indicator and observes a sticky sentinel; the
  // keyboard behaviour under test runs after that setup, so it has to survive.
  globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
  globalThis.requestAnimationFrame = (fn) => dom.window.setTimeout(fn, 0);
  globalThis.cancelAnimationFrame = (id) => dom.window.clearTimeout(id);
  globalThis.IntersectionObserver = class {
    observe() {}
    disconnect() {}
  };
  dom.window.IntersectionObserver = globalThis.IntersectionObserver;
  const mod = await import(`${pathToFileURL(resolve(JS, rel)).href}?a11y=${n++}`);
  return { mod, dom, document: dom.window.document, window: dom.window };
}

function press(window, target, key, { shift = false } = {}) {
  const event = new window.KeyboardEvent('keydown', {
    key,
    shiftKey: shift,
    bubbles: true,
    cancelable: true,
  });
  target.dispatchEvent(event);
  return event;
}

// ------------------------------------------------------------------- modal

const PAGE_WITH_TRIGGER =
  '<!doctype html><body><button id="opener">Open</button><a href="#x">a link</a></body>';

describe('modal focus management', () => {
  it('moves focus into the dialog when it opens', async () => {
    const { mod, document } = await load('ui/modal.js', PAGE_WITH_TRIGGER);
    mod.showModal({ title: 'Confirm', confirmLabel: 'Yes' });

    const dialog = document.querySelector('[role="dialog"]');
    assert.ok(dialog, 'no dialog rendered');
    assert.ok(
      dialog.contains(document.activeElement),
      `focus stayed outside the dialog (on ${document.activeElement?.id || 'body'})`,
    );
  });

  it('traps Tab inside the dialog', async () => {
    // aria-modal="true" says the rest of the page is inaccessible. Without a
    // trap, Tab from the last control lands on whatever follows in the
    // document -- the promise is made and immediately broken.
    const { mod, document, window } = await load('ui/modal.js', PAGE_WITH_TRIGGER);
    mod.showModal({ title: 'Confirm', confirmLabel: 'Yes', cancelLabel: 'No' });

    const dialog = document.querySelector('[role="dialog"]');
    const focusable = [...dialog.querySelectorAll('button, input, a[href]')];
    const last = focusable[focusable.length - 1];

    last.focus();
    press(window, last, 'Tab');

    assert.equal(
      document.activeElement,
      focusable[0],
      'Tab from the last control did not wrap to the first',
    );
  });

  it('traps Shift+Tab at the first control', async () => {
    const { mod, document, window } = await load('ui/modal.js', PAGE_WITH_TRIGGER);
    mod.showModal({ title: 'Confirm', confirmLabel: 'Yes', cancelLabel: 'No' });

    const dialog = document.querySelector('[role="dialog"]');
    const focusable = [...dialog.querySelectorAll('button, input, a[href]')];

    focusable[0].focus();
    press(window, focusable[0], 'Tab', { shift: true });

    assert.equal(
      document.activeElement,
      focusable[focusable.length - 1],
      'Shift+Tab from the first control did not wrap to the last',
    );
  });

  it('returns focus to whatever opened it', async () => {
    // Otherwise a keyboard user is dropped at the top of the document and has
    // to Tab back to where they were.
    const { mod, document } = await load('ui/modal.js', PAGE_WITH_TRIGGER);
    const opener = document.getElementById('opener');
    opener.focus();

    const pending = mod.showModal({ title: 'Confirm', confirmLabel: 'Yes' });
    document.getElementById('modal-confirm').click();
    await pending;

    assert.equal(
      document.activeElement,
      opener,
      'focus did not return to the control that opened the dialog',
    );
  });

  it('returns focus after Escape too, not only after a button', async () => {
    const { mod, document, window } = await load('ui/modal.js', PAGE_WITH_TRIGGER);
    const opener = document.getElementById('opener');
    opener.focus();

    const pending = mod.showModal({ title: 'Confirm', confirmLabel: 'Yes' });
    press(window, document, 'Escape');
    await pending;

    assert.equal(document.activeElement, opener);
  });

  it('makes the rest of the page inert while it is open', async () => {
    // aria-modal alone is advisory and unevenly supported. `inert` removes the
    // background from the tab order and the accessibility tree outright.
    const { mod, document } = await load('ui/modal.js', PAGE_WITH_TRIGGER);
    const pending = mod.showModal({ title: 'Confirm', confirmLabel: 'Yes' });

    assert.ok(
      document.getElementById('opener').closest('[inert]'),
      'the page behind the dialog is not inert',
    );

    document.getElementById('modal-confirm').click();
    await pending;

    assert.equal(
      document.querySelectorAll('body > [inert]').length,
      0,
      'inert was left behind after the dialog closed',
    );
  });

  it('is labelled by its own heading', async () => {
    // aria-labelledby over aria-label: one source of truth for the name, and
    // it stays correct if the heading is ever changed.
    const { mod, document } = await load('ui/modal.js', PAGE_WITH_TRIGGER);
    mod.showModal({ title: 'Delete suppression', confirmLabel: 'Yes' });

    const dialog = document.querySelector('[role="dialog"]');
    const labelledBy = dialog.getAttribute('aria-labelledby');
    assert.ok(labelledBy, 'the dialog has no aria-labelledby');

    const heading = document.getElementById(labelledBy);
    assert.ok(heading, `aria-labelledby points at #${labelledBy}, which is not there`);
    assert.match(heading.textContent, /Delete suppression/);
  });

  it('nests without stranding inert on the page', async () => {
    // Two dialogs in sequence must not leave the first one's inert behind.
    const { mod, document } = await load('ui/modal.js', PAGE_WITH_TRIGGER);
    for (let i = 0; i < 2; i += 1) {
      const pending = mod.showModal({ title: `Dialog ${i}`, confirmLabel: 'Yes' });
      document.getElementById('modal-confirm').click();
      await pending;
    }
    assert.equal(document.querySelectorAll('body > [inert]').length, 0);
  });
});

// -------------------------------------------------------------------- tabs

const TABLIST = `<!doctype html><body class="page-dashboard">
  <div class="tablist" role="tablist">
    <button class="tab" id="tab-overview" role="tab" aria-controls="overview" aria-selected="true">Overview</button>
    <button class="tab" id="tab-violations" role="tab" aria-controls="violations" aria-selected="false">Violations</button>
    <button class="tab" id="tab-dependencies" role="tab" aria-controls="dependencies" aria-selected="false">Dependencies</button>
    <button class="tab" id="tab-suppressions" role="tab" aria-controls="suppressions" aria-selected="false">Suppressions</button>
  </div>
  <div class="tab-panel-main active" id="overview" role="tabpanel"></div>
  <div class="tab-panel-main" id="violations" role="tabpanel"></div>
  <div class="tab-panel-main" id="dependencies" role="tabpanel"></div>
  <div class="tab-panel-main" id="suppressions" role="tabpanel"></div>
  <span class="tab-indicator"></span>
</body>`;

async function tablist() {
  const ctx = await load('ui/tabs.js', TABLIST);
  ctx.mod.initTabChrome();
  return ctx;
}

describe('tablist keyboard navigation', () => {
  it('moves to the next tab on ArrowRight', async () => {
    // The ARIA tabs pattern. Without it the only way to change tab is a click.
    const { document, window } = await tablist();
    const first = document.getElementById('tab-overview');
    first.focus();
    press(window, first, 'ArrowRight');

    assert.equal(document.activeElement.id, 'tab-violations');
    assert.equal(
      document.getElementById('tab-violations').getAttribute('aria-selected'),
      'true',
    );
  });

  it('moves to the previous tab on ArrowLeft', async () => {
    const { mod, document, window } = await tablist();
    mod.switchTab('violations');
    const current = document.getElementById('tab-violations');
    current.focus();
    press(window, current, 'ArrowLeft');

    assert.equal(document.activeElement.id, 'tab-overview');
  });

  it('wraps from the last tab to the first', async () => {
    const { mod, document, window } = await tablist();
    mod.switchTab('suppressions');
    const last = document.getElementById('tab-suppressions');
    last.focus();
    press(window, last, 'ArrowRight');

    assert.equal(document.activeElement.id, 'tab-overview');
  });

  it('wraps from the first tab to the last', async () => {
    const { document, window } = await tablist();
    const first = document.getElementById('tab-overview');
    first.focus();
    press(window, first, 'ArrowLeft');

    assert.equal(document.activeElement.id, 'tab-suppressions');
  });

  it('jumps to the first tab on Home and the last on End', async () => {
    const { mod, document, window } = await tablist();
    mod.switchTab('dependencies');
    const current = document.getElementById('tab-dependencies');

    current.focus();
    press(window, current, 'Home');
    assert.equal(document.activeElement.id, 'tab-overview');

    document.activeElement.focus();
    press(window, document.activeElement, 'End');
    assert.equal(document.activeElement.id, 'tab-suppressions');
  });

  it('leaves other keys alone', async () => {
    // A handler that swallowed everything would break typing in the page.
    const { document, window } = await tablist();
    const first = document.getElementById('tab-overview');
    first.focus();
    const event = press(window, first, 'a');
    assert.equal(event.defaultPrevented, false);
    assert.equal(document.activeElement.id, 'tab-overview');
  });

  it('keeps exactly one tab in the page tab order', async () => {
    // Roving tabindex: Tab reaches the tablist once and arrows move within it.
    // With all four tabbable, a keyboard user pays four stops to cross it.
    const { document } = await tablist();
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const tabbable = tabs.filter((t) => t.getAttribute('tabindex') === '0');

    assert.equal(tabbable.length, 1, 'exactly one tab should be tabbable');
    assert.equal(tabbable[0].getAttribute('aria-selected'), 'true');
    for (const other of tabs.filter((t) => t !== tabbable[0])) {
      assert.equal(other.getAttribute('tabindex'), '-1', `${other.id} is still tabbable`);
    }
  });

  it('moves the tabbable one along with the selection', async () => {
    const { mod, document } = await tablist();
    mod.switchTab('dependencies');

    assert.equal(document.getElementById('tab-dependencies').getAttribute('tabindex'), '0');
    assert.equal(document.getElementById('tab-overview').getAttribute('tabindex'), '-1');
  });

  it('labels each panel with the tab that controls it', async () => {
    // Otherwise a screen reader announces "tab panel" and nothing else.
    const { document } = await tablist();
    for (const tab of document.querySelectorAll('[role="tab"]')) {
      const panel = document.getElementById(tab.getAttribute('aria-controls'));
      assert.ok(panel, `${tab.id} controls a panel that is not there`);
      assert.equal(
        panel.getAttribute('aria-labelledby'),
        tab.id,
        `panel #${panel.id} is not labelled by its tab`,
      );
    }
  });
});

// ------------------------------------------------------------------ charts

describe('chart alternatives', () => {
  it('gives every canvas an accessible name and a role', async () => {
    // A bare <canvas> is an empty box to a screen reader. The three charts
    // carried no name, no role and no description of what they plot.
    const { readFileSync } = await import('node:fs');
    const template = resolve(
      dirname(fileURLToPath(import.meta.url)),
      '../../archguard/dashboard/templates/dashboard.html',
    );
    const html = readFileSync(template, 'utf8');

    const canvases = [...html.matchAll(/<canvas\b[^>]*>/g)].map((m) => m[0]);
    assert.ok(canvases.length >= 3, `expected the three charts, found ${canvases.length}`);
    for (const tag of canvases) {
      assert.match(tag, /role="img"/, `canvas without a role: ${tag}`);
      assert.match(tag, /aria-label=|aria-labelledby=/, `canvas without a name: ${tag}`);
    }
  });

  it('describes a rendered chart in text, not only in pixels', async () => {
    // The numbers behind the drawing, for anyone who cannot see it. Written
    // alongside each chart as it renders, so it cannot drift from the data.
    const { mod, document } = await load(
      'render/charts.js',
      '<!doctype html><body class="page-dashboard">' +
        '<canvas id="trendChart" role="img" aria-label="Health score over time"></canvas>' +
        '<div id="trendChart-desc" class="sr-only"></div>' +
        '</body>',
    );
    globalThis.Chart = class {
      constructor() {}
      update() {}
      destroy() {}
    };
    globalThis.Chart.defaults = { color: '', borderColor: '', font: { family: '' } };
    document.querySelector('canvas').getContext = () => ({
      createLinearGradient: () => ({ addColorStop() {} }),
      canvas: {},
    });

    mod.updateTrendChart([
      { timestamp: '2026-01-01T00:00:00Z', score: 80 },
      { timestamp: '2026-01-02T00:00:00Z', score: 87.5 },
    ]);

    const description = document.getElementById('trendChart-desc').textContent;
    assert.ok(description.length > 0, 'the chart has no text description');
    assert.match(description, /87\.5|87/, `the latest value is not described: ${description}`);
  });
});

// ---------------------------------------------------------------- aria-busy

describe('loading states', () => {
  it('marks the dashboard busy while a refresh is in flight', async () => {
    // A spinner is invisible to a screen reader. aria-busy on the region being
    // updated is what tells assistive technology to wait rather than announce
    // a half-written table.
    const { readFileSync } = await import('node:fs');
    const js = readFileSync(resolve(JS, 'poll.js'), 'utf8');
    assert.match(js, /aria-busy/, 'nothing sets aria-busy around a refresh');
  });

  it('clears busy again when the refresh finishes', async () => {
    const { readFileSync } = await import('node:fs');
    const js = readFileSync(resolve(JS, 'poll.js'), 'utf8');
    const sets = [...js.matchAll(/aria-busy['"],\s*['"](true|false)['"]/g)].map((m) => m[1]);
    assert.ok(sets.includes('true'), 'busy is never set');
    assert.ok(sets.includes('false'), 'busy is set but never cleared');
  });
});

// ------------------------------------------------------- AI availability

const AI_PAGE = `<!doctype html><body class="page-dashboard">
  <input id="advisor-question-input">
  <button id="btn-advisor-ask">Ask</button>
  <button id="remediation-btn">Generate Plan</button>
  <button id="violations-remediation-btn">Suggest fixes</button>
  <p id="ai-unavailable-note" hidden role="status"></p>
</body>`;

describe('AI availability in the interface', () => {
  it('disables the AI controls and says why', async () => {
    // Without this a visitor types a question, waits for a round trip, and is
    // told the feature was never going to work.
    const { mod, document } = await load('capabilities.js', AI_PAGE);
    mod.setAiEnabled(false, 'GEMINI_API_KEY is not set.');

    for (const id of ['btn-advisor-ask', 'advisor-question-input', 'remediation-btn']) {
      assert.equal(document.getElementById(id).disabled, true, `${id} still enabled`);
    }
    const note = document.getElementById('ai-unavailable-note');
    assert.equal(note.hidden, false);
    assert.match(note.textContent, /GEMINI_API_KEY/);
  });

  it('gives a disabled control a reason a screen reader can reach', async () => {
    // `disabled` says no without saying why; the tooltip is pointer-only.
    const { mod, document } = await load('capabilities.js', AI_PAGE);
    mod.setAiEnabled(false, 'The configured model is not offered.');

    const button = document.getElementById('btn-advisor-ask');
    assert.equal(button.getAttribute('aria-describedby'), 'ai-unavailable-note');
    assert.match(button.title, /configured model/);
  });

  it('leaves the controls alone when AI works', async () => {
    const { mod, document } = await load('capabilities.js', AI_PAGE);
    mod.setAiEnabled(false, 'nope');
    mod.setAiEnabled(true, '');

    assert.equal(document.getElementById('btn-advisor-ask').disabled, false);
    assert.equal(document.getElementById('ai-unavailable-note').hidden, true);
    assert.equal(document.getElementById('btn-advisor-ask').hasAttribute('title'), false);
  });

  it('escapes the reason rather than trusting it as markup', async () => {
    const { mod, document } = await load('capabilities.js', AI_PAGE);
    mod.setAiEnabled(false, '<img src=x onerror="alert(1)">');

    const note = document.getElementById('ai-unavailable-note');
    assert.equal(note.querySelector('img'), null, 'the reason was injected as markup');
  });
});
