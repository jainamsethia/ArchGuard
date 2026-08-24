/**
 * Focus management in the confirmation modal (P2-5).
 *
 * showModal() is what stands between a user and deleting a suppression, and it
 * announced itself as role="dialog" aria-modal="true" -- which tells assistive
 * technology that the rest of the page is inert and that focus is contained.
 * Neither was implemented: Tab walked straight out of the dialog into the page
 * behind it, and closing the dialog dropped focus to the top of the document,
 * so a keyboard user landed back at the start of the page with no idea whether
 * the thing they confirmed had happened.
 *
 * ARIA APG, Dialog (Modal) pattern:
 *   https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/
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

async function openModal(window, options) {
  const { showModal } = await window.module('ui/modal.js');
  const closed = showModal(options);
  // showModal builds synchronously and resolves only on close, so the DOM is
  // already in place here; awaiting `closed` would deadlock the test.
  return { closed, card: window.document.querySelector('.modal-card') };
}

function press(window, key, init = {}) {
  window.document.dispatchEvent(
    new window.KeyboardEvent('keydown', { key, bubbles: true, ...init }),
  );
}

/** A button in the page, standing in for the control that opened the dialog. */
function trigger(window) {
  const btn = window.document.createElement('button');
  btn.id = 'opener';
  btn.textContent = 'Remove';
  window.document.body.appendChild(btn);
  btn.focus();
  return btn;
}

describe('P2-5: modal focus containment', () => {
  it('moves focus into the dialog on open', async () => {
    const window = await loadDashboard({ respond });
    const { closed } = await openModal(window, { title: 'Remove suppression?' });

    assert.ok(
      window.document.querySelector('.modal-card').contains(window.document.activeElement),
      'focus stayed on the page behind the dialog',
    );

    press(window, 'Escape');
    await closed;
  });

  it('wraps Tab from the last control back to the first', async () => {
    const window = await loadDashboard({ respond });
    const { closed, card } = await openModal(window, {
      title: 'Suppress violation',
      input: { placeholder: 'Reason' },
    });

    const focusables = card.querySelectorAll('input, button');
    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    last.focus();
    press(window, 'Tab');
    assert.equal(
      window.document.activeElement,
      first,
      'Tab escaped the dialog -- aria-modal="true" promises it cannot',
    );

    press(window, 'Escape');
    await closed;
  });

  it('wraps Shift+Tab from the first control back to the last', async () => {
    const window = await loadDashboard({ respond });
    const { closed, card } = await openModal(window, {
      title: 'Suppress violation',
      input: { placeholder: 'Reason' },
    });

    const focusables = card.querySelectorAll('input, button');
    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    first.focus();
    press(window, 'Tab', { shiftKey: true });
    assert.equal(window.document.activeElement, last, 'Shift+Tab escaped the dialog backwards');

    press(window, 'Escape');
    await closed;
  });
});

describe('P2-5: modal focus restoration', () => {
  it('returns focus to the control that opened it', async () => {
    const window = await loadDashboard({ respond });
    const opener = trigger(window);

    const { closed } = await openModal(window, { title: 'Remove suppression?' });
    assert.notEqual(window.document.activeElement, opener, 'focus should have moved into the dialog');

    press(window, 'Escape');
    await closed;

    assert.equal(
      window.document.activeElement,
      opener,
      'focus was dropped to the document, so a keyboard user restarts from the top of the page',
    );
  });

  it('restores focus after confirming, not just after cancelling', async () => {
    const window = await loadDashboard({ respond });
    const opener = trigger(window);

    const { closed } = await openModal(window, { title: 'Remove suppression?' });
    window.document.getElementById('modal-confirm').click();
    await closed;

    assert.equal(window.document.activeElement, opener);
  });

  it('survives the opener being removed by the action it confirmed', async () => {
    const window = await loadDashboard({ respond });
    const opener = trigger(window);

    const { closed } = await openModal(window, { title: 'Remove suppression?' });
    // Exactly what removing a suppression does: the row holding the button
    // that opened the dialog is gone by the time the dialog closes.
    opener.remove();

    press(window, 'Escape');
    await assert.doesNotReject(closed, 'closing threw when the opener had been removed');
  });
});

describe('P2-5: modal accessible names', () => {
  it('names the dialog from its visible title', async () => {
    const window = await loadDashboard({ respond });
    const { closed, card } = await openModal(window, { title: 'Remove suppression?' });

    const labelledBy = card.getAttribute('aria-labelledby');
    assert.ok(labelledBy, 'the dialog has no aria-labelledby');
    const title = window.document.getElementById(labelledBy);
    assert.ok(title, `aria-labelledby points at #${labelledBy}, which does not exist`);
    assert.equal(title.textContent.trim(), 'Remove suppression?');

    press(window, 'Escape');
    await closed;
  });

  it('gives the text input a real label, not just a placeholder', async () => {
    const window = await loadDashboard({ respond });
    const { closed, card } = await openModal(window, {
      title: 'Suppress violation',
      input: { placeholder: 'Reason for suppressing' },
    });

    const field = card.querySelector('#modal-input');
    assert.ok(field, 'no input rendered');

    // A placeholder is not an accessible name: it disappears on the first
    // keystroke, and several screen readers never announce it at all.
    const label = card.querySelector('label[for="modal-input"]');
    const named = label || field.getAttribute('aria-label') || field.getAttribute('aria-labelledby');
    assert.ok(named, 'the input is named only by its placeholder');
    if (label) assert.ok(label.textContent.trim().length > 0, 'the label is empty');

    press(window, 'Escape');
    await closed;
  });

  it('associates the body text with the dialog', async () => {
    const window = await loadDashboard({ respond });
    const { closed, card } = await openModal(window, {
      title: 'Remove suppression?',
      body: 'This cannot be undone.',
    });

    const describedBy = card.getAttribute('aria-describedby');
    assert.ok(describedBy, 'the explanatory body text is not announced with the dialog');
    assert.equal(
      window.document.getElementById(describedBy).textContent.trim(),
      'This cannot be undone.',
    );

    press(window, 'Escape');
    await closed;
  });
});
