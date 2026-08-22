import { sanitize } from '../dom.js';


export function showModal({ title, body = '', input = null, confirmLabel = 'Confirm', cancelLabel = 'Cancel', destructive = false }) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        const inputHtml = input !== null
            ? `<input class="modal-input" id="modal-input" type="text" placeholder="${sanitize(input.placeholder || '')}" value="${sanitize(input.value || '')}"/>`
            : '';
        overlay.innerHTML = `
            <div class="modal-card" role="dialog" aria-modal="true" aria-label="${sanitize(title)}">
                <h3 class="modal-title">${sanitize(title)}</h3>
                ${body ? `<p class="modal-body">${sanitize(body)}</p>` : ''}
                ${inputHtml}
                <div class="modal-actions">
                    ${cancelLabel ? `<button class="btn-action" id="modal-cancel">${sanitize(cancelLabel)}</button>` : ''}
                    <button class="btn-action" id="modal-confirm" style="${destructive ? 'border-color:var(--danger-color);color:var(--danger-color);' : ''}">${sanitize(confirmLabel)}</button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);

        const cleanup = (val) => { overlay.remove(); document.removeEventListener('keydown', onKey); resolve(val); };
        const onKey = (e) => {
            if (e.key === 'Escape') cleanup(input !== null ? null : false);
            if (e.key === 'Enter' && input !== null) cleanup(document.getElementById('modal-input').value);
        };
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
