import { sanitize } from '../dom.js';


/** What Tab can reach inside a dialog, in document order. */
const FOCUSABLE =
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
    'textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

/** Counter for unique heading ids, so aria-labelledby resolves to one element. */
let dialogSeq = 0;


export function showModal({ title, body = '', input = null, confirmLabel = 'Confirm', cancelLabel = 'Cancel', destructive = false }) {
    return new Promise(resolve => {
        // Remembered before anything steals focus, and restored on the way out.
        // Closing used to drop focus on <body>, which returns a keyboard user
        // to the top of the document rather than to the control they pressed.
        const opener = document.activeElement;

        const titleId = `modal-title-${dialogSeq++}`;
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        const inputHtml = input !== null
            ? `<input class="modal-input" id="modal-input" type="text" placeholder="${sanitize(input.placeholder || '')}" value="${sanitize(input.value || '')}"/>`
            : '';
        overlay.innerHTML = `
            <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="${titleId}">
                <h3 class="modal-title" id="${titleId}">${sanitize(title)}</h3>
                ${body ? `<p class="modal-body">${sanitize(body)}</p>` : ''}
                ${inputHtml}
                <div class="modal-actions">
                    ${cancelLabel ? `<button class="btn-action" id="modal-cancel">${sanitize(cancelLabel)}</button>` : ''}
                    <button class="btn-action" id="modal-confirm" style="${destructive ? 'border-color:var(--danger-color);color:var(--danger-color);' : ''}">${sanitize(confirmLabel)}</button>
                </div>
            </div>
        `;

        // Everything already on the page goes inert for as long as the dialog
        // is up. `aria-modal` alone is advisory and unevenly honoured; `inert`
        // removes the background from the tab order and the accessibility tree
        // outright. Captured before the overlay is appended so it is not in the
        // list, and only elements this call inerted are released afterwards --
        // a nested dialog must not un-inert the page on the way out.
        const inerted = [...document.body.children].filter(
            (el) => !el.hasAttribute('inert'),
        );
        for (const el of inerted) el.setAttribute('inert', '');

        document.body.appendChild(overlay);

        const cleanup = (val) => {
            overlay.remove();
            document.removeEventListener('keydown', onKey);
            for (const el of inerted) el.removeAttribute('inert');
            // Only if focus is still inside the dialog we are closing, so a
            // handler that deliberately moved focus elsewhere is not overruled.
            if (opener && typeof opener.focus === 'function') opener.focus();
            resolve(val);
        };

        const onKey = (e) => {
            if (e.key === 'Escape') {
                cleanup(input !== null ? null : false);
                return;
            }
            if (e.key === 'Enter' && input !== null) {
                cleanup(document.getElementById('modal-input').value);
                return;
            }
            if (e.key === 'Tab') trapTab(e);
        };

        /** Keep Tab inside the dialog, wrapping at both ends. */
        function trapTab(e) {
            const stops = [...overlay.querySelectorAll(FOCUSABLE)];
            if (stops.length === 0) return;
            const first = stops[0];
            const last = stops[stops.length - 1];
            const active = document.activeElement;

            if (e.shiftKey && (active === first || !overlay.contains(active))) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && (active === last || !overlay.contains(active))) {
                e.preventDefault();
                first.focus();
            }
        }

        const cancelBtn = document.getElementById('modal-cancel');
        if (cancelBtn) cancelBtn.addEventListener('click', () => cleanup(input !== null ? null : false));
        document.getElementById('modal-confirm').addEventListener('click', () => {
            cleanup(input !== null ? document.getElementById('modal-input').value.trim() : true);
        });
        overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(input !== null ? null : false); });
        document.addEventListener('keydown', onKey);

        const inputEl = document.getElementById('modal-input');
        if (inputEl) inputEl.focus();
        else document.getElementById('modal-confirm').focus();
    });
}
