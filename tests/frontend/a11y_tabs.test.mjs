/**
 * Keyboard accessibility of the dashboard tablist (P2-5).
 *
 * The markup already carried role="tablist"/role="tab"/aria-selected, which is
 * what an automated axe scan checks and why this gap survived one. What axe
 * cannot check is the keyboard contract those roles promise: announcing a
 * widget as a tablist tells a screen-reader user that Arrow keys move between
 * tabs and that Tab moves past the whole set. Neither was true here -- all four
 * tabs sat in the tab order and the Arrow keys did nothing -- so the roles were
 * describing a widget the page did not implement.
 *
 * ARIA APG, Tabs pattern (automatic activation):
 *   https://www.w3.org/WAI/ARIA/apg/patterns/tabs/
 */

import { strict as assert } from 'node:assert';
import { describe, it } from 'node:test';

import { loadDashboard, run } from './harness.mjs';

function respond(url) {
  if (url.startsWith('/api/v1/runs?')) return { runs: [run()] };
  if (url.startsWith('/api/v1/runs/latest')) return run();
  if (url.startsWith('/api/v1/modules')) return { modules: {}, edges: [] };
  return {};
}

const TAB_IDS = ['tab-overview', 'tab-violations', 'tab-dependencies', 'tab-suppressions'];

/** Every tab button, in DOM order. */
function tabs(document) {
  return TAB_IDS.map((id) => document.getElementById(id));
}

function press(window, el, key) {
  el.dispatchEvent(new window.KeyboardEvent('keydown', { key, bubbles: true }));
}

describe('P2-5: tablist roving tabindex', () => {
  it('keeps exactly one tab in the page tab order', async () => {
    const window = await loadDashboard({ respond });
    const [overview, violations, dependencies, suppressions] = tabs(window.document);

    // The whole tablist is one tab stop. Four separate stops is what the
    // browser gives you for free with four <button>s, and it is precisely
    // what role="tablist" tells assistive tech is NOT happening.
    assert.equal(overview.getAttribute('tabindex'), '0', 'the selected tab must be the one tab stop');
    for (const tab of [violations, dependencies, suppressions]) {
      assert.equal(
        tab.getAttribute('tabindex'),
        '-1',
        `${tab.id} is still an independent tab stop`,
      );
    }
  });

  it('moves the tab stop with the selection', async () => {
    const window = await loadDashboard({ respond });
    const { document } = window;
    const { switchTab } = await window.module('ui/tabs.js');

    switchTab('dependencies');

    const [overview, , dependencies] = tabs(document);
    assert.equal(dependencies.getAttribute('tabindex'), '0');
    assert.equal(dependencies.getAttribute('aria-selected'), 'true');
    assert.equal(
      overview.getAttribute('tabindex'),
      '-1',
      'the previously selected tab kept the tab stop, so Tab would land on a tab that is not current',
    );
  });
});

describe('P2-5: tablist arrow-key navigation', () => {
  it('ArrowRight selects and focuses the next tab', async () => {
    const window = await loadDashboard({ respond });
    const { document } = window;
    const [overview, violations] = tabs(document);

    overview.focus();
    press(window, overview, 'ArrowRight');

    assert.equal(violations.getAttribute('aria-selected'), 'true');
    assert.equal(document.activeElement, violations, 'focus did not follow the selection');
  });

  it('ArrowLeft selects and focuses the previous tab', async () => {
    const window = await loadDashboard({ respond });
    const { document } = window;
    const [overview, violations] = tabs(document);
    const { switchTab } = await window.module('ui/tabs.js');

    switchTab('violations');
    violations.focus();
    press(window, violations, 'ArrowLeft');

    assert.equal(overview.getAttribute('aria-selected'), 'true');
    assert.equal(document.activeElement, overview);
  });

  it('wraps from the last tab to the first and back', async () => {
    const window = await loadDashboard({ respond });
    const { document } = window;
    const [overview, , , suppressions] = tabs(document);
    const { switchTab } = await window.module('ui/tabs.js');

    switchTab('suppressions');
    suppressions.focus();
    press(window, suppressions, 'ArrowRight');
    assert.equal(document.activeElement, overview, 'ArrowRight at the end should wrap to the first tab');

    press(window, overview, 'ArrowLeft');
    assert.equal(document.activeElement, suppressions, 'ArrowLeft at the start should wrap to the last tab');
  });

  it('Home and End jump to the first and last tab', async () => {
    const window = await loadDashboard({ respond });
    const { document } = window;
    const [overview, violations, , suppressions] = tabs(document);
    const { switchTab } = await window.module('ui/tabs.js');

    switchTab('violations');
    violations.focus();

    press(window, violations, 'End');
    assert.equal(document.activeElement, suppressions);
    assert.equal(suppressions.getAttribute('aria-selected'), 'true');

    press(window, suppressions, 'Home');
    assert.equal(document.activeElement, overview);
    assert.equal(overview.getAttribute('aria-selected'), 'true');
  });

  it('leaves other keys alone', async () => {
    const window = await loadDashboard({ respond });
    const { document } = window;
    const [overview] = tabs(document);

    overview.focus();
    press(window, overview, 'a');

    assert.equal(overview.getAttribute('aria-selected'), 'true', 'an unrelated key changed the selection');
    assert.equal(document.activeElement, overview);
  });
});

describe('P2-5: tabpanel relationships', () => {
  it('names every panel after the tab that controls it', async () => {
    const window = await loadDashboard({ respond });
    const { document } = window;

    for (const tab of tabs(document)) {
      const panel = document.getElementById(tab.getAttribute('aria-controls'));
      assert.ok(panel, `${tab.id} points at a panel that does not exist`);
      assert.equal(
        panel.getAttribute('aria-labelledby'),
        tab.id,
        `panel #${panel.id} has no accessible name, so it is announced as an unlabelled group`,
      );
    }
  });

  it('makes each panel focusable so the keyboard can reach its content', async () => {
    const window = await loadDashboard({ respond });
    const { document } = window;

    for (const tab of tabs(document)) {
      const panel = document.getElementById(tab.getAttribute('aria-controls'));
      // Without this, Tab from the tablist skips straight past a panel whose
      // first interactive element is far down the page -- or past one that has
      // no interactive element at all, leaving the content unreachable.
      assert.equal(panel.getAttribute('tabindex'), '0', `panel #${panel.id} is not reachable from the tablist`);
    }
  });

  it('gives the tablist an accessible name', async () => {
    const window = await loadDashboard({ respond });
    const list = window.document.querySelector('.page-dashboard .tablist');
    assert.ok(
      list.getAttribute('aria-label') || list.getAttribute('aria-labelledby'),
      'a tablist with no name is announced only as "tab list"',
    );
  });
});
