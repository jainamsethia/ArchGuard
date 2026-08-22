import { sanitize } from '../dom.js';


// Reads the provenance flags persisted on every run (see
// dashboard/_result_schema.py). When a run's contract came from the
// directory-name heuristic, the module map every score and violation is
// computed against was guessed rather than measured -- the user has to
// be told that plainly, and on every tab, not just at submission time.
export const FALLBACK_REASON_TEXT = {
    history_unavailable:
        'ArchGuard could not read this repository’s commit history, so it could not measure which files change together.',
    sparse_history:
        'This repository has too little commit history for ArchGuard to measure which files change together.',
    low_community_diversity:
        'This repository’s commit history did not show distinct groups of files that change together.'
};




// States what the score was graded against.
//
// When a repo ships no .archguard.yml the dashboard generates one, and
// it pins thresholds to a fixed profile. It deliberately does NOT derive
// them from the repo's own fan-out the way `archguard init` does: that
// baseline grades a repo against its own current state, so a first scan
// can only ever pass. Saying which policy was applied is the difference
// between "scored 100" and "scored 100 against these thresholds".
export const PROFILE_BLURB = {
    ci: 'balanced defaults',
    strict: 'production-grade defaults',
    lenient: 'minimal-enforcement defaults'
};




// Plain-language statement of which layers contributed real signal to
// this run's score, and why the others did not. The composite averages
// only the layers that ran, so presenting the number without this makes
// a check that had nothing to check look like a check that passed.
export const LAYER_PLAIN = {
    1: 'forbidden cross-module imports',
    2: 'module fan-out against its budget',
    3: 'semantic drift since the last scan',
    4: 'duplicated code across modules'
};



export function updateMetrics(latestRun) {
    if (!latestRun || latestRun.score === undefined || latestRun.score === null) return;

    document.getElementById('current-score').textContent = latestRun.score.toFixed(1);

    const bandEl = document.getElementById('current-band');
    bandEl.textContent = latestRun.band || 'UNKNOWN';

    // Color coding based on band
    if (latestRun.band === 'PASS' || latestRun.band === 'HEALTHY') bandEl.style.color = 'var(--success-color)';
    else if (latestRun.band === 'WARN' || latestRun.band === 'WATCH') bandEl.style.color = 'var(--warn-color)';
    else if (latestRun.band === 'FAIL' || latestRun.band === 'CRITICAL') bandEl.style.color = 'var(--danger-color)';

    const violations = latestRun.violations || [];
    document.getElementById('active-violations').textContent = violations.length;
}


export function updateProvenanceBanner(latestRun) {
    const banner = document.getElementById('fallback-banner');
    if (!banner) return;

    if (!latestRun || !latestRun.fallback_directory_heuristic) {
        banner.hidden = true;
        return;
    }

    // fallback_reason is persisted as the contract's generated_by
    // string, e.g. "archguard init (directory heuristic fallback: sparse_history)".
    const raw = String(latestRun.fallback_reason || '');
    const key = Object.keys(FALLBACK_REASON_TEXT).find(k => raw.includes(k));
    const why = key ? FALLBACK_REASON_TEXT[key] : 'ArchGuard could not measure this repository’s module boundaries from its commit history.';

    document.getElementById('fallback-banner-body').innerHTML =
        sanitize(why) +
        ' Modules below were instead <strong>inferred from top-level folder names</strong>. ' +
        'If this project’s real structure does not match its folder layout, the health score, ' +
        'grade, per-module scores and violations describe a module map that is not the real one — ' +
        'read them as indicative only.';
    banner.hidden = false;
}


export function updateThresholdNote(latestRun) {
    const note = document.getElementById('threshold-note');
    if (!note) return;

    const contract = (latestRun && latestRun.contract) || {};
    const profile = contract.profile;

    if (!latestRun || !latestRun.contract_auto_generated || !profile) {
        note.hidden = true;
        return;
    }

    // Read the real numbers off the contract rather than restating them
    // here, so this can't drift from archguard/profiles/defaults.py.
    const modules = Array.isArray(contract.modules) ? contract.modules : [];
    const budgets = modules.map(m => m.coupling_budget).filter(b => typeof b === 'number');
    const budget = budgets.length ? Math.max(...budgets) : null;
    const failAt = typeof contract.fail_threshold === 'number'
        ? Math.round((1 - contract.fail_threshold) * 100)
        : null;

    let text = 'No <code>.archguard.yml</code> was found in this repository, so it was measured against the '
        + `<strong>${sanitize(profile)}</strong> profile`;
    if (PROFILE_BLURB[profile]) text += ` (${PROFILE_BLURB[profile]})`;
    const bits = [];
    if (budget !== null) bits.push(`fan-out per module &le; ${budget}`);
    if (failAt !== null) bits.push(`health &ge; ${failAt} to pass`);
    if (bits.length) text += `: ${bits.join(', ')}`;
    text += '. These are fixed thresholds, not derived from this repository&rsquo;s own current structure.';

    document.getElementById('threshold-note-body').innerHTML = text;
    note.hidden = false;
}


export function updateLayerStatus(latestRun) {
    const box = document.getElementById('layer-status');
    if (!box) return;

    const layers = (latestRun && latestRun.layer_results) || [];
    if (!layers.length) {
        box.hidden = true;
        return;
    }

    const ran = layers.filter(l => !l.skipped);
    const list = document.getElementById('layer-status-list');
    list.innerHTML = '';

    for (const l of layers) {
        const what = LAYER_PLAIN[l.layer] || sanitize(l.name);
        const li = document.createElement('li');
        if (l.skipped) {
            const why = l.skip_reason ? sanitize(l.skip_reason) : 'not run';
            li.innerHTML = `<strong>Layer ${l.layer}</strong> &mdash; ${what}: `
                + `<em>not checked</em> (${why}). Excluded from the score.`;
        } else {
            const n = l.violation_count || 0;
            const found = n === 0 ? 'no findings' : `${n} finding${n === 1 ? '' : 's'}`;
            li.innerHTML = `<strong>Layer ${l.layer}</strong> &mdash; ${what}: `
                + `checked, ${found}.`;
        }
        list.appendChild(li);
    }

    // The cycle gate is evaluated outside the four layers, so it would
    // otherwise be invisible here despite capping the grade.
    const fitness = ((latestRun.metrics || {}).fitness_results) || [];
    for (const f of fitness) {
        const li = document.createElement('li');
        const verdict = f.passed ? 'passed' : '<strong>FAILED</strong>';
        li.innerHTML = `<strong>Gate</strong> &mdash; ${sanitize(f.name)}: ${verdict}`
            + (f.evidence ? ` (${sanitize(f.evidence)})` : '');
        list.appendChild(li);
    }

    document.getElementById('layer-status-intro').textContent =
        `The score below is an average over the ${ran.length} of ${layers.length} `
        + `layer${layers.length === 1 ? '' : 's'} that produced a signal on this run.`;
    box.hidden = false;
}


export function updateViolationCounts(latestRun) {
    const el = document.getElementById('violations-counts');
    if (!el) return;

    const sel = latestRun && latestRun.remediation_selection;
    if (!sel) {
        el.textContent = '';
        return;
    }

    // selected_violations, not selected: the latter also counts a failed
    // gate, which is sent to the LLM but is not a row in this table.
    const covered = sel.selected_violations != null ? sel.selected_violations : sel.selected;
    const parts = [`${sel.detected} violation${sel.detected === 1 ? '' : 's'} detected`];
    if (sel.suppressed > 0) parts.push(`${sel.suppressed} suppressed`);
    if (sel.eligible > covered) {
        parts.push(`AI fix suggestions available for the ${covered} highest-priority`);
    } else if (covered > 0) {
        parts.push(`AI fix suggestions available for all ${covered}`);
    }
    el.textContent = parts.join(' · ')
        + '. Every violation found is listed below regardless.';
}


export function renderEmptyState() {
    const container = document.querySelector('.dashboard-container');
    if (!container) return;

    let empty = document.getElementById('empty-state-panel');
    if (!empty) {
        empty = document.createElement('div');
        empty.id = 'empty-state-panel';
        empty.className = 'dashboard-container';
        empty.innerHTML = `
            <div class="header">
                <h1>ArchGuard Dashboard</h1>
            </div>
            <div class="empty-state">
                <div class="empty-icon">🏗️</div>
                <h3 class="empty-title">No analyses yet</h3>
                <p class="empty-body">
                    Analyze a GitHub repository to see architectural health data here.
                </p>
                <a href="/" class="btn-primary">Analyze a Repository →</a>
            </div>
        `;
        container.parentNode.insertBefore(empty, container.nextSibling);
    }
    container.hidden = true;
    hideRefreshLoader();
}


export function clearEmptyState() {
    const empty = document.getElementById('empty-state-panel');
    if (empty) empty.remove();
    const container = document.querySelector('.dashboard-container');
    if (container) container.hidden = false;
}


export function hideRefreshLoader() {
    const loader = document.getElementById('refresh-loader');
    if (loader) loader.style.display = 'none';
}
