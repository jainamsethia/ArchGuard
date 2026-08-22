import { state } from '../state.js';
import { jobQuery } from '../api.js';
import { getErrorStateHtml, sanitize } from '../dom.js';
import { updateViolationsTable } from '../render/violations.js';


export async function generateRemediationPlan() {
    const btn = document.getElementById('remediation-btn');
    const resultsEl = document.getElementById('remediation-results');

    btn.disabled = true;
    btn.textContent = 'Generating...';
    resultsEl.innerHTML = '<div style="color: var(--text-secondary);">Generating remediation plan...</div>';

    try {
        const res = await fetch(`/api/v1/remediation/plan${jobQuery}`);
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            let msg = `Error ${res.status}`;
            try {
                const body = JSON.parse(text);
                msg = body.detail || body.error || msg;
            } catch (_) {}
            resultsEl.innerHTML = getErrorStateHtml(msg, () => generateRemediationPlan());
            return;
        }
        const data = await res.json();
        const tasks = data.tasks || [];

        if (data.error) {
            resultsEl.innerHTML = getErrorStateHtml(data.error, () => generateRemediationPlan());
            return;
        }

        renderRemediationTasks(tasks, resultsEl);
    } catch (err) {
        console.error('Remediation error:', err);
        resultsEl.innerHTML = getErrorStateHtml('Error generating remediation plan.', () => generateRemediationPlan());
    } finally {
        btn.disabled = false;
        btn.textContent = 'Generate Plan';
    }
}


export async function generateViolationsRemediation() {
    const btn = document.getElementById('violations-remediation-btn');
    const resultsEl = document.getElementById('violations-remediation-results');

    if (!state.latestRun || !state.latestRun.violations || state.latestRun.violations.length === 0) {
        resultsEl.innerHTML = '<div style="color: var(--text-secondary);">No violations to suggest fixes for.</div>';
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Generating...';
    resultsEl.innerHTML = '<div style="color: var(--text-secondary);">Generating remediation suggestions...</div>';

    try {
        // GET, not POST-with-a-body: the server reads the persisted run
        // and applies its own ranking and suppression. Posting the
        // browser's copy of the list would let the client decide what
        // the LLM sees, and would hit the request's 50-violation cap on
        // any repo with more findings than that.
        const res = await fetch(`/api/v1/remediation/plan${jobQuery}`, {
            method: 'GET',
            headers: { 'Accept': 'application/json' }
        });
        if (!res.ok) {
            const text = await res.text().catch(() => '');
            let msg = `Error ${res.status}`;
            try {
                const body = JSON.parse(text);
                msg = body.detail || body.error || msg;
            } catch (_) {}
            resultsEl.innerHTML = getErrorStateHtml(msg, () => generateViolationsRemediation());
            return;
        }
        const data = await res.json();
        const tasks = data.tasks || [];

        if (data.error) {
            resultsEl.innerHTML = getErrorStateHtml(data.error, () => generateViolationsRemediation());
            return;
        }

        // Mark which rows the plan actually covered, using the server's
        // selection rather than re-deriving it here.
        if (data.selection && Array.isArray(data.selection.selected_keys)) {
            state.remediationSelectedKeys = new Set(data.selection.selected_keys);
            updateViolationsTable(state.latestRun);
        }

        renderRemediationTasks(tasks, resultsEl);
    } catch (err) {
        console.error('Violations remediation error:', err);
        resultsEl.innerHTML = getErrorStateHtml('Error generating remediation suggestions.', () => generateViolationsRemediation());
    } finally {
        btn.disabled = false;
        btn.textContent = 'Suggest fixes for these violations';
    }
}


export function renderRemediationTasks(tasks, targetEl) {
    if (!tasks || tasks.length === 0) {
        targetEl.innerHTML = '<div style="color: var(--success-color);">No remediation tasks needed. Architecture is healthy! 🎉</div>';
        return;
    }
    targetEl.innerHTML = tasks.map((t, i) => {
        const badgeClass = 'badge-' + (t.priority || 'medium');
        const criteria = (t.acceptance_criteria || []).map(c => `<li>${sanitize(c)}</li>`).join('');
        const borderColor = t.priority === 'critical' ? 'var(--danger-color)' : t.priority === 'high' ? 'var(--warn-color)' : 'var(--accent-color)';
        return `
            <div class="remediation-card" style="border-left-color: ${borderColor};">
                <div class="remediation-card-head">
                    <span class="remediation-card-title">${sanitize(t.title)}</span>
                    <div class="remediation-card-meta">
                        <span class="badge ${badgeClass}">${sanitize(t.priority)}</span>
                        <span class="remediation-card-effort">${t.effort_days || '?'}d</span>
                    </div>
                </div>
                <div class="remediation-card-desc">${sanitize(t.description)}</div>
                ${criteria ? `<ul class="remediation-card-criteria">${criteria}</ul>` : ''}
                ${targetBasisNote(t)}
            </div>
        `;
    }).join('');
}


export function targetBasisNote(task) {
    const grounded = task && task.target_basis === 'archguard_requirement';
    const label = grounded
        ? 'Target set by ArchGuard&rsquo;s configured limits'
        : 'Suggested target &mdash; not an ArchGuard requirement';
    const cls = grounded ? 'badge-low' : 'badge-medium';
    return `<div class="remediation-card-basis">`
        + `<span class="badge ${cls}">${label}</span></div>`;
}
