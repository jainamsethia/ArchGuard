import { state } from '../state.js';
import { highlightJobId } from '../api.js';
import { getEmptyStateHtml, getErrorStateHtml, sanitize } from '../dom.js';
import { updateTrendChart } from './charts.js';
import { violationKey } from './violations.js';


export function renderRepoHistoryTable(runs, tableEl) {
    if (!tableEl) return;
    if (!runs.length) {
        tableEl.innerHTML = getEmptyStateHtml('🕓', 'No History', 'No previous runs recorded for this repository yet.');
        return;
    }
    // Newest first.
    const sorted = [...runs].sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
    const rows = sorted.map(r => {
        const d = r.timestamp ? new Date(r.timestamp) : null;
        const when = d ? d.toLocaleString() : '—';
        const score = r.score != null ? Number(r.score).toFixed(1) : '—';
        const band = r.band || '—';
        const viol = r.violations ? r.violations.length : (r.total_violations ?? 0);
        const repo = (r.repo_url || '').replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '');
        const href = r.job_id ? `dashboard.html?job_id=${encodeURIComponent(r.job_id)}` : '#';
        return `
            <tr data-href="${href}">
                <td>${sanitize(when)}</td>
                <td><strong>${sanitize(score)}</strong></td>
                <td>${sanitize(band)}</td>
                <td>${sanitize(String(viol))}</td>
                <td style="color:var(--text-secondary)">${sanitize(repo)}</td>
            </tr>`;
    }).join('');
    tableEl.innerHTML = `
        <table class="repo-history-table">
            <thead><tr><th>When</th><th>Score</th><th>Band</th><th>Violations</th><th>Repo</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
    // Make rows clickable.
    tableEl.querySelectorAll('tr[data-href]').forEach(tr => {
        tr.addEventListener('click', () => {
            const href = tr.getAttribute('data-href');
            if (href && href !== '#') window.location.href = href;
        });
    });
}


export function _runLabel(r) {
    const d = r.timestamp ? new Date(r.timestamp) : null;
    const when = d ? `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2,'0')}` : '?';
    return `${r.score ?? '--'} (${r.band ?? '?'}) · ${when} · ${(r.repo_url||'').replace(/^https?:\/\/github\.com\//,'').replace(/\.git$/,'')}`;
}


export function populateRecentPicker(runs) {
    state.recentRuns = runs || [];
    const pickers = [
        document.getElementById('recent-analyses-picker'),
        document.getElementById('compare-picker'),
    ];
    pickers.forEach(p => {
        if (!p) return;
        const placeholder = p.querySelector('option')?.textContent || '';
        p.innerHTML = `<option value="">${placeholder}</option>`;
        for (const r of state.recentRuns) {
            if (!r.job_id) continue;
            const opt = document.createElement('option');
            opt.value = r.job_id;
            opt.textContent = _runLabel(r);
            p.appendChild(opt);
        }
    });
    // Pre-select the current highlighted run in the base picker.
    const base = document.getElementById('recent-analyses-picker');
    if (highlightJobId && base) base.value = highlightJobId;

    // Enable compare HERE, not in initCompareControls(). That runs on
    // DOMContentLoaded, before this async fetch has filled the picker,
    // so it always saw one option (the placeholder) and disabled the
    // button permanently -- the feature was unreachable.
    const toggle = document.getElementById('compare-toggle-btn');
    if (toggle && base) {
        const runCount = base.options.length - 1;  // minus the placeholder
        toggle.disabled = runCount < 2;
        toggle.title = toggle.disabled
            ? 'Need at least 2 analyses to compare'
            : 'Compare two runs side-by-side';
    }
}


export function initCompareControls() {
    const toggle = document.getElementById('compare-toggle-btn');
    const comparePicker = document.getElementById('compare-picker');
    const basePicker = document.getElementById('recent-analyses-picker');
    // Enablement is decided in populateRecentPicker(), once the runs
    // have actually loaded.
    if (toggle) toggle.addEventListener('click', () => {
        const on = !comparePicker.hidden;
        comparePicker.hidden = on;
        if (on) {
            // Hiding compare mode: clear and collapse.
            comparePicker.value = '';
            document.getElementById('compare-panel').hidden = true;
        } else if (basePicker && !basePicker.value) {
            // Need a base run to compare against.
            basePicker.focus();
        }
    });
    if (basePicker) basePicker.addEventListener('change', () => {
        const jid = basePicker.value;
        if (jid && jid !== highlightJobId) {
            window.location.href = `dashboard.html?job_id=${encodeURIComponent(jid)}`;
        }
    });
    if (comparePicker) comparePicker.addEventListener('change', () => {
        const a = basePicker?.value || highlightJobId;
        renderRunComparison(a, comparePicker.value);
    });
}


export async function renderRunComparison(jobIdA, jobIdB) {
    const panel = document.getElementById('compare-panel');
    const results = document.getElementById('compare-results');
    if (!jobIdA || !jobIdB || jobIdA === jobIdB) {
        if (panel) panel.hidden = true;
        return;
    }
    results.innerHTML = '<span class="text-helper">Loading comparison…</span>';
    panel.hidden = false;
    try {
        const [a, b] = await Promise.all([_fetchRunByJobId(jobIdA), _fetchRunByJobId(jobIdB)]);
        if (!a || !b) {
            results.innerHTML = getErrorStateHtml('Could not load both runs. Their workspace may have expired.', () => renderRunComparison(jobIdA, jobIdB));
            return;
        }
        const va = new Map((a.violations || []).map(v => [violationKey(v), v]));
        const vb = new Map((b.violations || []).map(v => [violationKey(v), v]));
        const added = [...vb.keys()].filter(k => !va.has(k)).map(k => vb.get(k));
        const removed = [...va.keys()].filter(k => !vb.has(k)).map(k => va.get(k));
        const persist = [...vb.keys()].filter(k => va.has(k)).map(k => vb.get(k));

        const rowHtml = (tag, v) => `
            <div class="compare-diff-row">
                <span class="compare-diff-tag ${tag}">${tag === 'persist' ? 'same' : tag}</span>
                <span>L${sanitize(v.layer||'?')} · ${sanitize(v.severity||'low')} · ${sanitize(v.file||v.module||'Global')} · ${sanitize(v.message||'')}</span>
            </div>`;
        const diffHtml = [
            ...added.map(v => rowHtml('added', v)),
            ...removed.map(v => rowHtml('removed', v)),
            ...persist.map(v => rowHtml('persist', v)),
        ].join('') || '<div class="text-helper">No violations in either run.</div>';

        results.innerHTML = `
            <p class="compare-summary">
                <strong>${sanitize(_runLabel(b))}</strong> vs <strong>${sanitize(_runLabel(a))}</strong> —
                <span style="color:var(--danger-color)">${added.length} new</span>,
                <span style="color:var(--success-color)">${removed.length} resolved</span>,
                <span>${persist.length} unchanged</span>
            </p>
            <div class="compare-diff">${diffHtml}</div>`;
    } catch (e) {
        results.innerHTML = getErrorStateHtml('Comparison failed: ' + e.message, () => renderRunComparison(jobIdA, jobIdB));
    }
}


export async function loadRepoHistory(repoUrl) {
    const container = document.getElementById('repo-history-container');
    const tableEl = document.getElementById('repo-history-table');
    if (container) container.hidden = false;
    if (tableEl) tableEl.innerHTML = '<span class="text-helper">Loading…</span>';
    const res = await fetch(`/api/v1/repos/${repoUrl.split('/').map(encodeURIComponent).join('/')}/runs`);
    if (!res.ok) {
        if (tableEl) tableEl.innerHTML = getErrorStateHtml(`Could not load history (HTTP ${res.status}).`, () => loadRepoHistory(repoUrl));
        return;
    }
    const data = await res.json();
    updateTrendChart(data.runs);  // reuse existing chart-rendering function; do not duplicate its logic
    renderRepoHistoryTable(data.runs || [], tableEl);
}


export async function _fetchRunByJobId(jobId) {
    // /api/v1/runs?job_id=... returns all entries for that job; take the newest.
    const res = await fetch(`/api/v1/runs?limit=50&job_id=${jobId}`);
    if (!res.ok) return null;
    const data = await res.json();
    const rs = data.runs || [];
    return rs.length ? rs[rs.length - 1] : null;
}
