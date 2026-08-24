import { sanitize } from '../dom.js';


/**
 * Everything inside the dialog that can hold focus.
 *
 * Used to find the ends of the tab ring. `:not([disabled])` matters because a
 * disabled confirm button would otherwise be treated as the last stop and
 * swallow the wrap.
 */
const FOCUSABLE = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    '[tabindex]:not([tabindex="-1"])',
].join(', ');


export function showModal({ title, body = '', input = null, confirmLabel = 'Confirm', cancelLabel = 'Cancel', destructive = false }) {
    return new Promise(resolve => {
        // Captured before the dialog exists, so it is the control the user was
        // actually on -- the Remove button in a suppression row, say.
        const previouslyFocused = document.activeElement;

        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        const inputHtml = input !== null
            ? `<label class="sr-only" for="modal-input">${sanitize(input.label || title)}</label>`
              + `<input class="modal-input" id="modal-input" type="text" placeholder="${sanitize(input.placeholder || '')}" value="${sanitize(input.value || '')}"/>`
            : '';
        // aria-labelledby/-describedby rather than aria-label: the title and
        // body are already on screen, and pointing at them keeps the announced
        // name identical to the visible one instead of maintaining a second
        // copy that can drift.
        overlay.innerHTML = `
            <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="modal-title"${body ? ' aria-describedby="modal-body"' : ''}>
                <h3 class="modal-title" id="modal-title">${sanitize(title)}</h3>
                ${body ? `<p class="modal-body" id="modal-body">${sanitize(body)}</p>` : ''}
                ${inputHtml}
                <div class="modal-actions">
                    ${cancelLabel ? `<button class="btn-action" id="modal-cancel">${sanitize(cancelLabel)}</button>` : ''}
                    <button class="btn-action" id="modal-confirm" style="${destructive ? 'border-color:var(--danger-color);color:var(--danger-color);' : ''}">${sanitize(confirmLabel)}</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        // Scoped to the overlay rather than the document: the ids above are
        // fixed, and a stale one left anywhere else in the page would
        // otherwise win the lookup.
        const card = overlay.querySelector('.modal-card');
        const $ = (sel) => card.querySelector(sel);

        const cleanup = (val) => {
            overlay.remove();
            document.removeEventListener('keydown', onKey);
            // Send focus back where it came from. Without this it falls to
            // <body>, which puts a keyboard user at the top of the page with
            // no indication that the thing they confirmed took effect.
            //
            // The opener is often gone by now -- confirming "remove" deletes
            // the row holding the button that opened this dialog -- so check
            // it is still in the document before reaching for it.
            if (previouslyFocused && previouslyFocused.isConnected && typeof previouslyFocused.focus === 'function') {
                previouslyFocused.focus();
            }
            resolve(val);
        };

        /**
         * Contain Tab within the dialog.
         *
         * aria-modal="true" tells assistive technology the rest of the page is
         * unavailable; a browser still happily tabs into it. Wrapping at both
         * ends is what makes the announcement true.
         */
        const trapTab = (e) => {
            const items = Array.from(card.querySelectorAll(FOCUSABLE));
            if (items.length === 0) {
                e.preventDefault();
                return;
            }
            const first = items[0];
            const last = items[items.length - 1];
            const active = document.activeElement;
            const outside = !card.contains(active);

            if (e.shiftKey && (active === first || outside)) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && (active === last || outside)) {
                e.preventDefault();
                first.focus();
            }
        };

        const onKey = (e) => {
            if (e.key === 'Escape') { cleanup(input !== null ? null : false); return; }
            if (e.key === 'Enter' && input !== null) { cleanup($('#modal-input').value); return; }
            if (e.key === 'Tab') trapTab(e);
        };

        const cancelBtn = $('#modal-cancel');
        if (cancelBtn) cancelBtn.addEventListener('click', () => cleanup(input !== null ? null : false));
        $('#modal-confirm').addEventListener('click', () => {
            cleanup(input !== null ? $('#modal-input').value.trim() : true);
        });
        overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(input !== null ? null : false); });
        document.addEventListener('keydown', onKey);

        const inputEl = $('#modal-input');
        if (inputEl) inputEl.focus();
        else $('#modal-confirm').focus();
    });
}
