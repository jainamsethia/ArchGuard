import { getEmptyStateHtml, sanitize, setBusy } from '../dom.js';
import { showModal } from '../ui/modal.js';


export async function loadSuppressions() {
    const tbody = document.querySelector('#suppressionsTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" class="text-helper-center">Loading...</td></tr>';
    setBusy(tbody, true);

    try {
        const urlParams = new URLSearchParams(window.location.search);
        const jobId = urlParams.get('job_id');
        const q = jobId ? `?job_id=${jobId}` : '';
        const res = await fetch(`/api/v1/suppressions${q}`);

        if (!res.ok) {
            if (res.status === 404 || res.status === 410) {
                 tbody.innerHTML = '<tr><td colspan="6" class="text-helper-center">Analysis workspace not found. Suppressions are bound to a specific working directory.</td></tr>';
                 return;
            }
            throw new Error(`HTTP ${res.status}`);
        }

        const data = await res.json();
        const suppressions = data.suppressions || [];

        if (suppressions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" style="padding:0; border:none;">' + getEmptyStateHtml('✓', 'No Suppressions', 'You have no active suppressions. Violations can be suppressed from the Violations tab.') + '</td></tr>';
            return;
        }

        tbody.innerHTML = suppressions.map(s => {
            const isExpired = s.expires_at && new Date(s.expires_at) < new Date();
            const rawDateStr = s.expires_at ? new Date(s.expires_at).toLocaleDateString() : 'Never';

            return `
            <tr style="${isExpired || !s.active ? 'opacity: 0.5' : ''}">
                <td>${sanitize(s.module)}</td>
                <td>L${sanitize(s.layer)}</td>
                <td><code style="font-size: 0.8em; color: var(--text-secondary);">${sanitize(s.id).substring(0, 8)}...</code></td>
                <td>${sanitize(s.reason)}</td>
                <td>${isExpired ? '<span style="color:var(--danger-color)">Expired</span>' : rawDateStr}</td>
                <td>
                    <button class="btn-action" data-suppression-id="${sanitize(s.id)}" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;">Remove</button>
                </td>
            </tr>
        `}).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" style="color:var(--danger-color)">Failed to load suppressions: ${e.message}</td></tr>`;
    } finally {
        // A `finally`, not a line at the end of the try: the 404/410 and
        // empty-list paths above both return early from inside it, and those
        // are exactly the cases that would strand the table permanently busy.
        setBusy(tbody, false);
    }
}


export async function removeSuppression(id) {
    const confirmed = await showModal({
        title: 'Remove suppression?',
        body: 'The violation will reappear on the next analysis.',
        confirmLabel: 'Remove',
        cancelLabel: 'Cancel',
        destructive: true,
    });
    if (!confirmed) return;

    try {
        const urlParams = new URLSearchParams(window.location.search);
        const jobId = urlParams.get('job_id');
        const q = jobId ? `?job_id=${jobId}` : '';

        const res = await fetch(`/api/v1/suppressions${q}`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ suppression_id: id })
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        loadSuppressions();
    } catch (e) {
        await showModal({
            title: 'Could not remove suppression',
            body: e.message,
            confirmLabel: 'OK',
            cancelLabel: '',
        });
    }
}


export async function createSuppression(module, layer, message) {
    const reason = await showModal({
        title: 'Suppress this violation',
        body: `Module: ${sanitize(module)} · Layer ${sanitize(layer)} — ${sanitize(message).slice(0, 120)}`,
        input: { placeholder: "Reason (e.g. 'False positive', 'Planned for Q3')" },
        confirmLabel: 'Suppress',
        cancelLabel: 'Cancel',
    });
    if (!reason) return;

    try {
        const urlParams = new URLSearchParams(window.location.search);
        const jobId = urlParams.get('job_id');
        const q = jobId ? `?job_id=${jobId}` : '';

        const res = await fetch(`/api/v1/suppressions${q}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                module: module,
                layer: parseInt(layer),
                message: message,
                reason: reason
            })
        });

        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        await showModal({
            title: 'Suppression added',
            body: 'Run analysis again to update scores.',
            confirmLabel: 'OK',
            cancelLabel: '',
        });
        loadSuppressions();
    } catch (e) {
        await showModal({
            title: 'Could not create suppression',
            body: e.message,
            confirmLabel: 'OK',
            cancelLabel: '',
        });
    }
}
