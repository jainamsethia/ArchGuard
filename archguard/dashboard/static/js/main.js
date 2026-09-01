import { state } from './state.js';
import { sendAdvisorQuestion } from './features/advisor.js';
import { generateRemediationPlan, generateViolationsRemediation } from './features/remediation.js';
import { applyCapabilities } from './capabilities.js';
import { fetchData, initVisibilityPolling, startPolling } from './poll.js';
import { configureChartDefaults } from './render/charts.js';
import { initCompareControls, loadRepoHistory } from './render/compare.js';
import { analyzePRRisk, initDependencyGraph, scanDependencies } from './render/deps.js';
import { startEvolutionAnalysis } from './render/evolution.js';
import { createSuppression, loadSuppressions, removeSuppression } from './render/suppressions.js';
import { updateViolationsTable } from './render/violations.js';
import { loadWatchState, saveWatchSettings, toggleWatch, unwatch } from './render/watch.js';
import { initNavigation, initTabChrome, onTabActivated, switchTab } from './ui/tabs.js';


/**
 * The dashboard's entry point, and the only module that touches wiring.
 *
 * Everything else exports functions and imports what it needs. This is where
 * DOM listeners are attached, where the tab chrome learns what each tab should
 * load, and where the first fetch is kicked off -- so a reader looking for
 * "what happens when the page opens" has one file to read, and a test can
 * import any other module without a page existing at all.
 *
 * Split out of a single 2,174-line dashboard.js in which all 56 functions and
 * six pieces of mutable state shared one script scope.
 */



export function initActionButtons() {
    const startEvo = document.getElementById('start-evolution-btn');
    if (startEvo) startEvo.addEventListener('click', startEvolutionAnalysis);

    const scanDeps = document.getElementById('scan-deps-btn');
    if (scanDeps) scanDeps.addEventListener('click', scanDependencies);

    const repoBtn = document.getElementById('repo-history-btn');
    if (repoBtn) repoBtn.addEventListener('click', () => {
        if (state.latestRun && state.latestRun.repo_url) {
            loadRepoHistory(state.latestRun.repo_url);
        }
    });

    const advKey = document.getElementById('advisor-question-input');
    if (advKey) advKey.addEventListener('keydown', (e) => { if(e.key==='Enter') sendAdvisorQuestion() });

    const advBtn = document.getElementById('btn-advisor-ask');
    if (advBtn) advBtn.addEventListener('click', sendAdvisorQuestion);

    const remBtn = document.getElementById('remediation-btn');
    if (remBtn) remBtn.addEventListener('click', generateRemediationPlan);

    const violRemBtn = document.getElementById('violations-remediation-btn');
    if (violRemBtn) violRemBtn.addEventListener('click', generateViolationsRemediation);

    const riskBtn = document.getElementById('analyze-risk-btn');
    if (riskBtn) riskBtn.addEventListener('click', analyzePRRisk);

    const watchBtn = document.getElementById('watch-toggle-btn');
    if (watchBtn) watchBtn.addEventListener('click', toggleWatch);

    const watchSaveBtn = document.getElementById('watch-save-btn');
    if (watchSaveBtn) watchSaveBtn.addEventListener('click', saveWatchSettings);

    const watchUnwatchBtn = document.getElementById('watch-unwatch-btn');
    if (watchUnwatchBtn) watchUnwatchBtn.addEventListener('click', unwatch);

    const refreshSupBtn = document.getElementById('refresh-suppressions-btn');
    if (refreshSupBtn) refreshSupBtn.addEventListener('click', loadSuppressions);

    // initCompareControls() is NOT called here. init() calls it, immediately
    // after this function, and calling it from both attached two click
    // listeners to the same button: the second read the state the first had
    // just written, so every click undid itself and Compare did nothing at
    // all. The button was enabled, which is all anything checked.

    // Delegated removal for per-row suppression "Remove" buttons (rendered
    // dynamically, so bind once on the static tbody).
    const supTbody = document.querySelector('#suppressionsTable tbody');
    if (supTbody) supTbody.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-suppression-id]');
        if (!btn) return;
        removeSuppression(btn.dataset.suppressionId);
    });

    // Delegated "Suppress" handler for per-violation buttons in the violations
    // table (rows are re-rendered on every fetch / page change, so delegate).
    const violTbody = document.querySelector('#violationsTable tbody');
    if (violTbody) violTbody.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-suppress');
        if (!btn || btn.disabled) return;
        createSuppression(btn.dataset.module, btn.dataset.layer, btn.dataset.message);
    });

    // Violations toolbar: re-render (resetting to page 1) on filter/sort change.
    const reRenderViolations = () => {
        state.currentViolationsPage = 1;
        if (state.latestRun) updateViolationsTable(state.latestRun);
    };
    ['violations-filter', 'violations-layer-filter', 'violations-sort'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', reRenderViolations);
        if (el) el.addEventListener('change', reRenderViolations);
    });

    // Export the current run's violations as a JSON download.
    const exportBtn = document.getElementById('violations-export-btn');
    if (exportBtn) exportBtn.addEventListener('click', () => {
        const vs = (state.latestRun && state.latestRun.violations) || [];
        const payload = JSON.stringify({
            job_id: state.latestRun?.job_id,
            score: state.latestRun?.score,
            band: state.latestRun?.band,
            violations: vs,
        }, null, 2);
        const blob = new Blob([payload], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `archguard-violations-${state.latestRun?.job_id || 'run'}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    });
}



/**
 * Attach everything and start.
 *
 * Exported rather than run on import, so importing this module in a test does
 * not immediately reach for elements and fire network calls. The page calls it
 * once; see the bottom of this file.
 */
export function init() {
    configureChartDefaults();
    initNavigation();
    initActionButtons();
    initCompareControls();

    // Each tab says what it needs the first time it is shown. tabs.js does not
    // import these modules -- it would depend on two features that depend on
    // it back -- so the mapping is declared here instead.
    onTabActivated('dependencies', initDependencyGraph);
    onTabActivated('suppressions', loadSuppressions);

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initTabChrome);
    } else {
        initTabChrome();
    }

    // Restore the tab named in the URL fragment, so a shared link opens where
    // the sender was looking.
    window.addEventListener('load', () => {
        const hash = window.location.hash.substring(1);
        if (['overview', 'violations', 'dependencies', 'suppressions'].includes(hash)) {
            switchTab(hash);
        }
    });

    // Before the first paint of the AI panels, so the controls are never
    // offered in a state where pressing them can only fail.
    applyCapabilities();

    initVisibilityPolling();
    fetchData();
    startPolling();
}

init();
