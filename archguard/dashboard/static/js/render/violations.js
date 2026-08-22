import { state } from '../state.js';
import { getEmptyStateHtml, getSeverityClass, sanitize, violationLocationCell } from '../dom.js';


export function violationKey(v) {
    return [v.module || '', v.layer || '', v.message || ''].join('|');
}


export function applyViolationsView(violations) {
    const filterEl = document.getElementById('violations-filter');
    const layerEl = document.getElementById('violations-layer-filter');
    const sortEl = document.getElementById('violations-sort');
    const q = (filterEl?.value || '').trim().toLowerCase();
    const layerFilter = layerEl?.value || '';
    const sortBy = sortEl?.value || 'severity';

    let view = violations.filter(v => {
        if (layerFilter && String(v.layer) !== layerFilter) return false;
        if (!q) return true;
        const hay = `${v.file || ''} ${v.module || ''} ${v.message || ''} ${v.severity || ''}`.toLowerCase();
        return hay.includes(q);
    });

    view = view.slice().sort((a, b) => {
        if (sortBy === 'severity') {
            const ra = SEVERITY_RANK[(a.severity || 'low').toLowerCase()] ?? 9;
            const rb = SEVERITY_RANK[(b.severity || 'low').toLowerCase()] ?? 9;
            if (ra !== rb) return ra - rb;
        }
        if (sortBy === 'layer') {
            const la = Number(a.layer) || 0, lb = Number(b.layer) || 0;
            if (la !== lb) return la - lb;
        }
        if (sortBy === 'file') {
            return String(a.file || a.module || '').localeCompare(String(b.file || b.module || ''));
        }
        return 0;
    });
    return view;
}


export function updateViolationsTable(latestRun) {
    const tbody = document.querySelector('#violationsTable tbody');
    const allViolations = latestRun.violations || [];
    const violations = applyViolationsView(allViolations);

    if (allViolations.length > 0 && violations.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="padding:0; border:none;">' + getEmptyStateHtml('🔍', 'No Matches', 'No violations match the current filter. Adjust the filter to see more.') + '</td></tr>';
        const controlContainer = document.getElementById('violations-pagination');
        if(controlContainer) controlContainer.innerHTML = '';
        return;
    }

    if (allViolations.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="padding:0; border:none;">' + getEmptyStateHtml('✅', 'No Violations', 'No active architecture violations. Great job!') + '</td></tr>';
        const controlContainer = document.getElementById('violations-pagination');
        if(controlContainer) controlContainer.innerHTML = '';
        return;
    }

    const totalPages = Math.ceil(violations.length / state.violationsPerPage);
    if (state.currentViolationsPage > totalPages) {
        state.currentViolationsPage = totalPages;
    }

    const startIdx = (state.currentViolationsPage - 1) * state.violationsPerPage;
    const endIdx = startIdx + state.violationsPerPage;
    const pageViolations = violations.slice(startIdx, endIdx);

    tbody.innerHTML = pageViolations.map(v => {
        // Only offer suppression when we have enough identity to match
        // the violation (module + layer + message is the store's key).
        const canSuppress = !!(v.module && v.layer && v.message);
        const suppressAttrs = canSuppress
            ? `data-module="${sanitize(v.module)}" data-layer="${sanitize(v.layer)}" data-message="${sanitize(v.message)}"`
            : 'disabled style="opacity:0.4; cursor:not-allowed;" title="This violation has no stable module/layer key to suppress."';
        // Plain-language explanation comes from the server
        // (archguard.analysis.plain_language) so the wording cannot
        // drift between the API and the page. The technical message and
        // raw numbers stay visible alongside it, not replaced by it.
        const plain = v.plain || null;
        const plainHtml = plain ? `
                <div class="violation-plain">
                    <div class="violation-plain-title">${sanitize(plain.title)}</div>
                    <div class="violation-plain-body">${sanitize(plain.body)}</div>
                </div>` : '';
        const techHtml = plain && plain.technical_details
            ? `<div class="violation-tech">Technical details: ${sanitize(plain.technical_details)}</div>`
            : '';
        const covered = state.remediationSelectedKeys.has(violationKey(v))
            ? '<span class="badge badge-low" title="An AI fix suggestion was generated for this violation">AI fix suggested</span>'
            : '';
        return `
        <tr>
            <td>L${sanitize(v.layer || '?')}</td>
            <td><span class="badge ${getSeverityClass(v.severity)}">${sanitize(v.severity || 'low')}</span> ${covered}</td>
            <td style="word-break: break-all;">${violationLocationCell(v)}</td>
            <td>
                <div class="violation-message">${sanitize(v.message || 'Unknown violation')}</div>
                ${plainHtml}
                ${techHtml}
            </td>
            <td><button class="btn-action btn-suppress" ${suppressAttrs} style="padding: 0.25rem 0.5rem; font-size: 0.75rem;">Suppress</button></td>
        </tr>
    `;
    }).join('');

    // Update pagination controls
    let controlContainer = document.getElementById('violations-pagination');
    if (!controlContainer) {
        controlContainer = document.createElement('div');
        controlContainer.id = 'violations-pagination';
        controlContainer.style = 'display: flex; justify-content: space-between; align-items: center; margin-top: 1rem; color: var(--text-secondary); font-size: 0.85rem;';
        document.querySelector('#violationsTable').parentElement.appendChild(controlContainer);
        controlContainer.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-page]');
            if (!btn || btn.disabled) return;
            if (btn.dataset.page === 'prev') state.currentViolationsPage--;
            else if (btn.dataset.page === 'next') state.currentViolationsPage++;
            updateViolationsTable(state.latestRun);
        });
    }

    if (violations.length > state.violationsPerPage) {
        controlContainer.innerHTML = `
            <div>Showing ${startIdx + 1}-${Math.min(endIdx, violations.length)} of ${violations.length} violations</div>
            <div style="display: flex; gap: 0.5rem;">
                <button class="btn-action" data-page="prev" ${state.currentViolationsPage === 1 ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}>Prev</button>
                <button class="btn-action" data-page="next" ${state.currentViolationsPage === totalPages ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}>Next</button>
            </div>
        `;
    } else {
        controlContainer.innerHTML = `<div>Showing all ${violations.length} violations</div>`;
    }
}
