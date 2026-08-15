

// Initialization
document.addEventListener("DOMContentLoaded", () => {
    initNavigation();
    initActionButtons();
});

function initNavigation() {
    // Quick links
    const fitnessLink = document.getElementById('chip-fitness');
    if (fitnessLink) fitnessLink.addEventListener('click', () => document.getElementById('fitness-container').scrollIntoView({behavior: 'smooth'}));

    const advisorLink = document.getElementById('chip-advisor');
    if (advisorLink) advisorLink.addEventListener('click', () => document.getElementById('advisor-panel').scrollIntoView({behavior: 'smooth'}));

    const evolutionLink = document.getElementById('chip-evolution');
    if (evolutionLink) evolutionLink.addEventListener('click', () => document.getElementById('evolution-trends-grid').scrollIntoView({behavior: 'smooth'}));

    // Tabs
    const overviewTab = document.getElementById('tab-overview');
    if (overviewTab) overviewTab.addEventListener('click', () => switchTab('overview'));

    const violationsTab = document.getElementById('tab-violations');
    if (violationsTab) violationsTab.addEventListener('click', () => switchTab('violations'));

    const depsTab = document.getElementById('tab-dependencies');
    if (depsTab) depsTab.addEventListener('click', () => switchTab('dependencies'));

    const suppressTab = document.getElementById('tab-suppressions');
    if (suppressTab) suppressTab.addEventListener('click', () => switchTab('suppressions'));
}

function initActionButtons() {
    const startEvo = document.getElementById('start-evolution-btn');
    if (startEvo) startEvo.addEventListener('click', startEvolutionAnalysis);

    const scanDeps = document.getElementById('scan-deps-btn');
    if (scanDeps) scanDeps.addEventListener('click', scanDependencies);

    const repoBtn = document.getElementById('repo-history-btn');
    if (repoBtn) repoBtn.addEventListener('click', () => {
        if (window.latestRun && window.latestRun.repo_url) {
            loadRepoHistory(window.latestRun.repo_url);
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

    const refreshSupBtn = document.getElementById('refresh-suppressions-btn');
    if (refreshSupBtn) refreshSupBtn.addEventListener('click', loadSuppressions);

    initCompareControls();

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
        window.currentViolationsPage = 1;
        if (window.latestRun) updateViolationsTable(window.latestRun);
    };
    ['violations-filter', 'violations-layer-filter', 'violations-sort'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.addEventListener('input', reRenderViolations);
        if (el) el.addEventListener('change', reRenderViolations);
    });

    // Export the current run's violations as a JSON download.
    const exportBtn = document.getElementById('violations-export-btn');
    if (exportBtn) exportBtn.addEventListener('click', () => {
        const vs = (window.latestRun && window.latestRun.violations) || [];
        const payload = JSON.stringify({
            job_id: window.latestRun?.job_id,
            score: window.latestRun?.score,
            band: window.latestRun?.band,
            violations: vs,
        }, null, 2);
        const blob = new Blob([payload], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `archguard-violations-${window.latestRun?.job_id || 'run'}.json`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    });
}


// Extracted inline script

        let trendChartInstance = null;
        let moduleChartInstance = null;
        let evolutionChartInstance = null;

        // Configuration for dark mode charts
        Chart.defaults.color = '#94a3b8';
        Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.1)';
        Chart.defaults.font.family = "'Inter', -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif";

        const urlParams = new URLSearchParams(window.location.search);
        const highlightJobId = urlParams.get('job_id');
        
        // Use highlightJobId for initial fetch queries
        const jobQuery = highlightJobId ? `?job_id=${highlightJobId}` : '';
        const jobQueryAmp = highlightJobId ? `&job_id=${highlightJobId}` : '';

        // job_id intentionally kept in the URL so the page is bookmarkable/refreshable

        async function safeFetch(url, fallback) {
            try {
                const resp = await fetch(url);
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                return await resp.json();
            } catch (e) {
                console.warn(`[dashboard] ${url} failed:`, e.message);
                // Replace any remaining skeleton placeholders with '--' and mark as errored
                document.querySelectorAll('.skeleton').forEach(el => {
                    el.classList.remove('skeleton');
                    el.textContent = '--';
                    el.title = `Failed to load: ${e.message}`;
                });
                return fallback;
            }
        }

        function renderEmptyState() {
            const mainContainer = document.querySelector('.metrics-grid').parentElement;
            mainContainer.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem;">
                    <h1 style="margin: 0; font-size: 2.5rem; background: linear-gradient(to right, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">ArchGuard Dashboard</h1>
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
            document.getElementById('refresh-loader').style.display = 'none';
        }

        async function fetchData() {
            document.getElementById('refresh-loader').style.display = 'inline-block';
            try {
                const [runsData, latestData, modulesData, evolutionData, gitEvoData] = await Promise.all([
                    safeFetch(`/api/v1/runs?limit=30${jobQueryAmp}`, { runs: [] }),
                    safeFetch(`/api/v1/runs/latest${jobQuery}`, null),
                    safeFetch(`/api/v1/modules${jobQuery}`, { modules: [] }),
                    safeFetch(`/api/v1/evolution/trends${jobQuery}`, { trends: [] }),
                    safeFetch(`/api/v1/evolution/latest${jobQuery}`, null)
                ]);

                if ((!runsData?.runs?.length && !latestData) || (latestData && latestData.empty)) {
                    updateProvenanceBanner(null);
                    updateThresholdNote(null);
                    updateLayerStatus(null);
                    renderEmptyState();
                    return;
                }

                if (highlightJobId) {
                    safeFetch(`/api/v1/jobs/${highlightJobId}`, null).then(job => {
                        const overview = document.getElementById('overview');
                        if (overview && !document.getElementById('job-banner')) {
                            const banner = document.createElement('div');
                            banner.id = 'job-banner';
                            banner.className = 'glass-card job-banner';
                            banner.style.background = 'rgba(16, 185, 129, 0.2)';
                            banner.style.borderColor = 'rgba(16, 185, 129, 0.4)';

                            const isSuccess = job && job.status === 'complete';
                            const repoLabel = job?.github_url
                                ? job.github_url.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '')
                                : 'your repository';
                            const heading = isSuccess
                                ? `✓ Analysis complete for ${sanitize(repoLabel)}`
                                : `Status: ${sanitize(job?.status || 'unknown')}`;

                            banner.innerHTML = `
                                <h3>${heading}</h3>
                                <p>Scroll down for the full breakdown.</p>
                            `;

                            overview.insertBefore(banner, overview.firstChild);
                            setTimeout(() => {
                                banner.style.background = 'var(--surface-color)';
                                banner.style.borderColor = 'var(--border-color)';
                            }, 3000);
                        }
                    });
                }

                updateProvenanceBanner(latestData);
                updateThresholdNote(latestData);
                updateLayerStatus(latestData);

                if (latestData) {
                    window.latestRun = latestData;
                    updateMetrics(latestData);
                    updateFitnessPanel(latestData);
                    updateViolationCounts(latestData);
                    updateViolationsTable(latestData);
                }
                updateTrendChart(runsData.runs);
                populateRecentPicker(runsData.runs);
                updateModuleChart(modulesData?.modules);
                updateEvolutionTrends(evolutionData);
                if (gitEvoData && gitEvoData.snapshots && gitEvoData.snapshots.length > 0) {
                    _applyGitEvolutionData(gitEvoData);
                }

                document.getElementById('last-updated').textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
            } catch (error) {
                console.error("Error fetching dashboard data:", error);
                document.getElementById('last-updated').textContent = 'Error updating';
            } finally {
                document.getElementById('refresh-loader').style.display = 'none';
            }
        }

        async function initDependencyGraph() {
            if (window._visNet) {
                window._visNet.redraw();
                window._visNet.fit();
                return;
            }
            const container = document.getElementById('dep-graph-container');
            if (!container) return;

            container.innerHTML = '<div style="color:var(--text-secondary);padding:2rem;text-align:center;">Loading dependency data\u2026</div>';

            let modulesData;
            try {
                const res = await fetch(`/api/v1/modules${jobQuery}`);
                modulesData = res.ok ? await res.json() : null;
            } catch (e) {
                container.innerHTML = '<div style="color:var(--text-secondary);padding:2rem;text-align:center;">Could not load dependency data.</div>';
                return;
            }

            if (!modulesData || !modulesData.modules || Object.keys(modulesData.modules).length === 0) {
                container.innerHTML = getEmptyStateHtml('📦', 'No Dependencies', 'Run an analysis to see the dependency graph.');
                return;
            }

            container.innerHTML = '';

            const modules = modulesData.modules;
            const nodes = Object.entries(modules).map(([name, score]) => ({
                id: name,
                label: name.split('.').pop(),
                title: `${sanitize(name)}\nScore: ${sanitize(String(score))}`,
                value: Math.max(10, (score || 0) * 2),
                color: score > 70 ? '#34d399' : score > 40 ? '#f59e0b' : '#ef4444',
            }));

            const edgeList = (modulesData.edges || []).map(e => ({
                from: e.from,
                to: e.to,
                arrows: 'to',
                color: { color: 'rgba(255, 255, 255, 0.2)' }
            }));

            const data = {
                nodes: new vis.DataSet(nodes),
                edges: new vis.DataSet(edgeList),
            };
            const options = {
                nodes: { shape: 'dot', font: { color: '#94a3b8', size: 12 } },
                edges: { color: { color: 'rgba(255,255,255,0.15)' } },
                physics: { stabilization: { iterations: 100 } },
            };
            window._visNet = new vis.Network(container, data, options);
        }

        function updateMetrics(latestRun) {
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

        // Reads the provenance flags persisted on every run (see
        // dashboard/_result_schema.py). When a run's contract came from the
        // directory-name heuristic, the module map every score and violation is
        // computed against was guessed rather than measured -- the user has to
        // be told that plainly, and on every tab, not just at submission time.
        const FALLBACK_REASON_TEXT = {
            history_unavailable:
                'ArchGuard could not read this repository’s commit history, so it could not measure which files change together.',
            sparse_history:
                'This repository has too little commit history for ArchGuard to measure which files change together.',
            low_community_diversity:
                'This repository’s commit history did not show distinct groups of files that change together.'
        };

        function updateProvenanceBanner(latestRun) {
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

        // States what the score was graded against.
        //
        // When a repo ships no .archguard.yml the dashboard generates one, and
        // it pins thresholds to a fixed profile. It deliberately does NOT derive
        // them from the repo's own fan-out the way `archguard init` does: that
        // baseline grades a repo against its own current state, so a first scan
        // can only ever pass. Saying which policy was applied is the difference
        // between "scored 100" and "scored 100 against these thresholds".
        const PROFILE_BLURB = {
            ci: 'balanced defaults',
            strict: 'production-grade defaults',
            lenient: 'minimal-enforcement defaults'
        };

        function updateThresholdNote(latestRun) {
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

        // Plain-language statement of which layers contributed real signal to
        // this run's score, and why the others did not. The composite averages
        // only the layers that ran, so presenting the number without this makes
        // a check that had nothing to check look like a check that passed.
        const LAYER_PLAIN = {
            1: 'forbidden cross-module imports',
            2: 'module fan-out against its budget',
            3: 'semantic drift since the last scan',
            4: 'duplicated code across modules'
        };

        function updateLayerStatus(latestRun) {
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

        // Identity of a violation row, matching archguard.analysis.ranking.finding_key.
        // Used to mark which rows an AI fix plan actually covered.
        function violationKey(v) {
            return [v.module || '', v.layer || '', v.message || ''].join('|');
        }

        // Which findings the last "Suggest fixes" run covered. Populated from the
        // server's own ranking, never recomputed here -- the browser must not
        // decide what counts as high priority.
        window.remediationSelectedKeys = new Set();

        function updateViolationCounts(latestRun) {
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

        function getSeverityClass(severity) {
            const s = (severity || 'low').toLowerCase();
            if (s === 'critical') return 'badge-critical';
            if (s === 'high') return 'badge-high';
            if (s === 'medium') return 'badge-medium';
            return 'badge-low';
        }

        // Build the "Location" cell for a violation. Layer 1 carries a real
        // source line (from tree-sitter), so we show `file:line`. Layers 2, 3,
        // and 4 are inherently module-level with no single line — we show the
        // module name rather than fabricate a line number.
        function violationLocationCell(v) {
            const file = v.file;
            const line = Number(v.line || 0);
            if (file && line > 0) {
                return `${sanitize(file)}:${line}`;
            }
            if (file) {
                return sanitize(file);
            }
            if (v.module) {
                return `${sanitize(v.module)} <span class="violation-loc-note">(module-level)</span>`;
            }
            return 'Global';
        }

        function sanitize(str) {
            if (str === null || str === undefined) return '';
            const div = document.createElement('div');
            div.textContent = String(str);
            return div.innerHTML
                .replaceAll('"', '&quot;')
                .replaceAll("'", '&#39;');
        }

        function getErrorStateHtml(message, retryFn) {
            const retryId = 'retry-' + Math.random().toString(36).slice(2, 9);
            setTimeout(() => {
                const btn = document.getElementById(retryId);
                if (btn) btn.addEventListener('click', retryFn);
            }, 0);
            return `
                <div class="empty-state error-state">
                    <div class="empty-icon">⚠️</div>
                    <h3 class="empty-title">Something went wrong</h3>
                    <p class="empty-body">${sanitize(message)}</p>
                    <button id="${retryId}" class="btn-action">Retry</button>
                </div>
            `;
        }

        // In-page modal dialog (replaces native prompt/alert/confirm) so the
        // interaction matches the dark glassmorphic system instead of a raw
        // browser dialog. Returns a Promise<string|boolean|null>:
        //   - confirm modal: true / false
        //   - input modal: the entered string, or null if cancelled
        function showModal({ title, body = '', input = null, confirmLabel = 'Confirm', cancelLabel = 'Cancel', destructive = false }) {
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

        function updateFitnessPanel(latestRun) {
            const container = document.getElementById('fitness-container');

            // Plain-language gloss for each built-in layer, so a first-time
            // user knows what "Layer 3" actually checks without leaving the page.
            // NOTE: keep in sync with the static glossary <dl> in dashboard.html.
            const LAYER_GLOSSES = {
                1: 'Flags forbidden or unapproved cross-module imports (tree-sitter AST).',
                2: 'Flags modules whose fan-out (unique dependencies) exceeds their coupling budget.',
                3: 'Flags modules whose embedding centroid drifted beyond the threshold since the baseline.',
                4: 'Flags cross-module function duplication found via vector similarity (FAISS).',
            };

            let layerResultsHtml = '';
            const layerResults = latestRun.layer_results || [];
            if (layerResults.length > 0) {
                const lrHtml = layerResults.map(lr => {
                    const gloss = LAYER_GLOSSES[lr.layer] || '';
                    const glossHtml = gloss ? `<div class="violation-loc-note" style="margin-top:0.25rem;">${gloss}</div>` : '';
                    if (lr.skipped) {
                        return `
                            <div class="fitness-card skipped">
                                <div class="fitness-card-header">
                                    <span class="fitness-card-title">➖ Layer ${sanitize(lr.layer.toString())} (${sanitize(lr.name)})</span>
                                    <span class="badge skipped">not checked, ${sanitize(lr.skip_reason || 'optional dependency not installed')}</span>
                                </div>
                                ${glossHtml}
                            </div>
                        `;
                    } else {
                        const statusClass = lr.score > 0 ? 'fail' : 'pass';
                        const icon = lr.score > 0 ? '❌' : '✅';
                        return `
                            <div class="fitness-card ${statusClass}">
                                <div class="fitness-card-header">
                                    <span class="fitness-card-title">${icon} Layer ${sanitize(lr.layer.toString())} (${sanitize(lr.name)})</span>
                                    <span class="badge ${statusClass}">Debt: ${sanitize((lr.score * 100).toFixed(1))}%</span>
                                </div>
                                ${glossHtml}
                            </div>
                        `;
                    }
                }).join('');
                layerResultsHtml = `
                    <h3 style="margin-bottom: 0.25rem; font-size: 1rem; color: var(--text-secondary);">Built-in Architecture Checks</h3>
                    <p style="margin: 0 0 1rem 0; font-size: 0.8rem; color: var(--text-secondary);">Lower is better — 0 means no issues detected. Health Score is the inverse of the weighted ArchDebt across these layers.</p>
                    <div class="fitness-grid" style="margin-bottom: 2rem;">
                        ${lrHtml}
                    </div>
                `;
            }

            const metrics = latestRun.metrics || {};
            const fitnessResults = metrics.fitness_results || [];
            let fitnessHtml = '';

            if (fitnessResults.length === 0) {
                fitnessHtml = `
                    <h3 style="margin-bottom: 1rem; font-size: 1rem; color: var(--text-secondary);">Your Custom Fitness Functions</h3>
                    ${getEmptyStateHtml('📋', 'No custom fitness functions yet', 'Add one in .archguard.yml to get automated pass/fail checks for rules specific to your architecture.')}
                `;
            } else {
                const html = fitnessResults.map(r => {
                    const passed = r.passed !== false;
                    const severity = r.severity || 'warn';
                    let statusClass = 'pass';
                    let icon = '✅';
                    
                    if (!passed) {
                        if (severity === 'critical') {
                            statusClass = 'fail';
                            icon = '❌';
                        } else {
                            statusClass = 'warn';
                            icon = '⚠️';
                        }
                    }
                    
                    const rule = sanitize(r.rule || '');
                    const name = sanitize(r.name || r.rule || 'Unknown');
                    const evidence = sanitize(r.evidence || '');
                    
                    let evidenceHtml = '';
                    if (!passed && evidence) {
                        evidenceHtml = `<div class="fitness-card-evidence">${evidence}</div>`;
                    }
                    
                    return `
                        <div class="fitness-card ${statusClass}">
                            <div class="fitness-card-header">
                                <span class="fitness-card-title">${icon} ${name}</span>
                                <span class="fitness-card-rule" title="${rule}">${rule}</span>
                            </div>
                            ${evidenceHtml}
                        </div>
                    `;
                }).join('');
                fitnessHtml = `
                    <h3 style="margin-bottom: 1rem; font-size: 1rem; color: var(--text-secondary);">Your Custom Fitness Functions</h3>
                    <div class="fitness-grid">${html}</div>
                `;
            }

            container.innerHTML = layerResultsHtml + fitnessHtml;
        }

        window.currentViolationsPage = 1;
        window.violationsPerPage = 20;

        const SEVERITY_RANK = { critical: 0, high: 1, medium: 2, low: 3 };

        // Apply the current text filter, layer filter, and sort to a violation
        // list. Returns a new array (does not mutate the run's data).
        function applyViolationsView(violations) {
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

        function updateViolationsTable(latestRun) {
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

            const totalPages = Math.ceil(violations.length / window.violationsPerPage);
            if (window.currentViolationsPage > totalPages) {
                window.currentViolationsPage = totalPages;
            }

            const startIdx = (window.currentViolationsPage - 1) * window.violationsPerPage;
            const endIdx = startIdx + window.violationsPerPage;
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
                const covered = window.remediationSelectedKeys.has(violationKey(v))
                    ? '<span class="badge badge-low" title="An AI fix suggestion was generated for this violation">AI fix suggested</span>'
                    : '';
                return `
                <tr>
                    <td>L${sanitize(v.layer || '?')}</td>
                    <td><span class="badge ${getSeverityClass(v.severity)}">${sanitize(v.severity || 'low')}</span> ${covered}</td>
                    <td style="color: #cbd5e1; word-break: break-all;">${violationLocationCell(v)}</td>
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
                    if (btn.dataset.page === 'prev') window.currentViolationsPage--;
                    else if (btn.dataset.page === 'next') window.currentViolationsPage++;
                    updateViolationsTable(window.latestRun);
                });
            }

            if (violations.length > window.violationsPerPage) {
                controlContainer.innerHTML = `
                    <div>Showing ${startIdx + 1}-${Math.min(endIdx, violations.length)} of ${violations.length} violations</div>
                    <div style="display: flex; gap: 0.5rem;">
                        <button class="btn-action" data-page="prev" ${window.currentViolationsPage === 1 ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}>Prev</button>
                        <button class="btn-action" data-page="next" ${window.currentViolationsPage === totalPages ? 'disabled style="opacity:0.5; cursor:not-allowed;"' : ''}>Next</button>
                    </div>
                `;
            } else {
                controlContainer.innerHTML = `<div>Showing all ${violations.length} violations</div>`;
            }
        }

        function updateTrendChart(runs) {
            if (!runs || runs.length < 2) {
                const ctx = document.getElementById('trendChart');
                if(ctx && ctx.parentElement) ctx.parentElement.innerHTML = getEmptyStateHtml('📈', 'No Trends', 'Not enough historical data to display a trend.');
                return;
            }

            // Sort runs chronologically
            const sortedRuns = [...runs].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

            const labels = sortedRuns.map(r => {
                const d = new Date(r.timestamp);
                return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${d.getMinutes().toString().padStart(2, '0')}`;
            });
            const data = sortedRuns.map(r => r.score || 0);

            if (trendChartInstance) {
                trendChartInstance.data.labels = labels;
                trendChartInstance.data.datasets[0].data = data;
                trendChartInstance.update();
                return;
            }

            const ctx = document.getElementById('trendChart').getContext('2d');

            // Create gradient
            const gradient = ctx.createLinearGradient(0, 0, 0, 400);
            gradient.addColorStop(0, 'rgba(59, 130, 246, 0.5)');
            gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');

            trendChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Health Score',
                        data: data,
                        borderColor: '#3b82f6',
                        backgroundColor: gradient,
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        pointBackgroundColor: '#0f172a',
                        pointBorderColor: '#3b82f6',
                        pointBorderWidth: 2,
                        pointRadius: 4,
                        pointHoverRadius: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            mode: 'index',
                            intersect: false,
                            backgroundColor: 'rgba(15, 23, 42, 0.9)',
                            titleColor: '#f8fafc',
                            bodyColor: '#cbd5e1',
                            borderColor: 'rgba(255,255,255,0.1)',
                            borderWidth: 1
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            grid: { color: 'rgba(255, 255, 255, 0.05)' }
                        },
                        x: {
                            grid: { display: false },
                            ticks: { maxTicksLimit: 8 }
                        }
                    }
                }
            });
        }

        function updateModuleChart(modulesData) {
            if (!modulesData || Object.keys(modulesData).length === 0) {
                const ctx = document.getElementById('moduleChart');
                if(ctx && ctx.parentElement) ctx.parentElement.innerHTML = getEmptyStateHtml('🧩', 'No Modules', 'Module complexity data is not available.');
                return;
            }

            const labels = Object.keys(modulesData);
            const data = Object.values(modulesData);

            // Sort by score descending
            const combined = labels.map((l, i) => ({label: l, data: data[i]}));
            combined.sort((a, b) => b.data - a.data);

            const noteEl = document.getElementById('module-chart-note');
            if (noteEl) {
                if (combined.length > 10) {
                    noteEl.textContent = `Showing top 10 of ${combined.length} modules by health score.`;
                } else {
                    noteEl.textContent = '';
                }
            }

            const sortedLabels = combined.slice(0, 10).map(x => x.label);
            const sortedData = combined.slice(0, 10).map(x => x.data);

            if (moduleChartInstance) {
                moduleChartInstance.data.labels = sortedLabels;
                moduleChartInstance.data.datasets[0].data = sortedData;
                moduleChartInstance.update();
                return;
            }

            const ctx = document.getElementById('moduleChart').getContext('2d');
            moduleChartInstance = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: sortedLabels,
                    datasets: [{
                        label: 'Module Score',
                        data: sortedData,
                        backgroundColor: 'rgba(139, 92, 246, 0.6)',
                        borderColor: 'rgba(139, 92, 246, 1)',
                        borderWidth: 1,
                        borderRadius: 4
                    }]
                },
                options: {
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    scales: {
                        x: {
                            beginAtZero: true,
                            grid: { color: 'rgba(255, 255, 255, 0.05)' }
                        },
                        y: {
                            grid: { display: false }
                        }
                    }
                }
            });
        }

        function updateEvolutionTrends(evoData) {
            function updateCard(type, trend) {
                const valEl = document.getElementById(`trend-${type}-val`);
                const statusEl = document.getElementById(`trend-${type}-status`);
                
                if (!trend || trend.current_value === null || trend.current_value === undefined) {
                    valEl.textContent = 'N/A';
                    statusEl.textContent = 'Insufficient data';
                    statusEl.style.color = 'var(--text-secondary)';
                    return;
                }
                
                // Format value depending on type
                if (type === 'violation') {
                    valEl.textContent = Math.round(trend.current_value);
                } else if (type === 'fitness') {
                    valEl.textContent = `${(trend.current_value * 100).toFixed(1)}%`;
                } else {
                    valEl.textContent = trend.current_value.toFixed(2);
                }

                const cls = trend.classification;
                let icon = '';
                let color = 'var(--text-secondary)';

                if (cls === 'improving') {
                    icon = '↑ Improving';
                    color = 'var(--success-color)';
                } else if (cls === 'declining') {
                    icon = '↓ Declining';
                    color = 'var(--danger-color)';
                } else if (cls === 'insufficient') {
                    icon = 'N/A — Insufficient data';
                } else {
                    icon = '→ Stable';
                }
                
                statusEl.textContent = icon;
                statusEl.style.color = color;
            }
            
            updateCard('health', evoData.health_trend);
            updateCard('debt', evoData.debt_trend);
            updateCard('violation', evoData.violation_trend);
            updateCard('fitness', evoData.fitness_trend);
        }

        async function scanDependencies() {
            const btn = document.getElementById('scan-deps-btn');
            const statusEl = document.getElementById('deps-status');
            const scoreEl = document.getElementById('deps-score');
            const tableContainer = document.getElementById('deps-table-container');
            const tbody = document.querySelector('#depsTable tbody');
            
            btn.disabled = true;
            btn.textContent = "Scanning...";
            statusEl.textContent = "Running pip-audit... (this may take a while)";
            
            try {
                const res = await fetch(`/api/v1/deps${jobQuery}`);
                if (!res.ok) {
                    statusEl.textContent = `Error ${res.status}: could not scan dependencies.`;
                    scoreEl.textContent = '--';
                    tableContainer.style.display = 'none';
                    return;
                }
                const data = await res.json();
                
                if (data.skipped) {
                    statusEl.textContent = `Skipped: ${data.skip_reason}`;
                    scoreEl.textContent = '--';
                    tableContainer.style.display = 'none';
                } else {
                    statusEl.textContent = `Scanned ${data.scanned_packages} packages. Found ${data.vulnerable_packages.length} vulnerabilities.`;
                    scoreEl.textContent = data.score.toFixed(1);
                    
                    if (data.vulnerable_packages.length > 0) {
                        tbody.innerHTML = data.vulnerable_packages.map(v => `
                            <tr>
                                <td style="font-weight: 600;">${sanitize(v.package)}</td>
                                <td>${sanitize(v.version)}</td>
                                <td><span class="badge badge-critical">${sanitize(v.id)}</span></td>
                                <td style="font-size: 0.875rem; color: var(--text-secondary);">${sanitize(v.description)}</td>
                            </tr>
                        `).join('');
                        tableContainer.style.display = 'block';
                    } else {
                        tableContainer.style.display = 'none';
                        statusEl.textContent += " All dependencies are healthy! 🎉";
                    }
                }
            } catch (err) {
                console.error("Dependency scan failed:", err);
                statusEl.textContent = "Error scanning dependencies.";
            } finally {
                btn.disabled = false;
                btn.textContent = "Scan Dependencies";
            }
        }

        
function getEmptyStateHtml(icon, title, body) {
    return `
        <div class="empty-state" style="margin-top:0;">
            <div class="empty-icon">${icon}</div>
            <h3 class="empty-title">${title}</h3>
            <p class="empty-body">${body}</p>
        </div>
    `;
}

        function switchTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.setAttribute('aria-selected', 'false'));
            document.querySelectorAll('.tab-panel-main').forEach(p => p.classList.remove('active'));
            
            const targetTab = document.querySelector(`.tab[aria-controls="${name}"]`);
            if (targetTab) {
                targetTab.setAttribute('aria-selected', 'true');
            }
            const targetPanel = document.getElementById(name);
            if (targetPanel) {
                targetPanel.classList.add('active');
            }
            
            history.pushState({}, '', window.location.search + `#${name}`);
            
            // Lazy-load each tab's data here, not in the click handler: tabs are
            // also opened by URL hash on load and by hashchange, and those paths
            // would otherwise leave the panel stuck on its "Loading..." row.
            if (name === 'dependencies') {
                initDependencyGraph();
            } else if (name === 'suppressions') {
                loadSuppressions();
            }
        }

        window.addEventListener('load', () => {
            const hash = window.location.hash.substring(1);
            if (['overview', 'violations', 'dependencies', 'suppressions'].includes(hash)) {
                switchTab(hash);
            }
        });

        window.addEventListener('hashchange', () => {
            const hash = window.location.hash.substring(1);
            if (['overview', 'violations', 'dependencies', 'suppressions'].includes(hash)) {
                switchTab(hash);
            } else if (!hash) {
                switchTab('overview');
            }
        });

        // Initial 
        async function startEvolutionAnalysis() {
            const btn = document.getElementById('start-evolution-btn');
            
            btn.disabled = true;
            btn.textContent = "Analyzing...";
            
            try {
                const res = await fetch(`/api/v1/evolution/analyze${jobQuery}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ max_commits: 10 })
                });
                const data = await res.json();
                
                if (data.error) {
                    document.getElementById('trend_direction').textContent = data.message || data.error;
                } else {
                    _applyGitEvolutionData(data);
                }
            } catch (err) {
                console.error("Evolution analysis failed:", err);
                document.getElementById('trend_direction').textContent = "Request failed.";
            } finally {
                btn.disabled = false;
                btn.textContent = "Analyze Git History";
            }
        }
        
        function _applyGitEvolutionData(data) {
            const velEl = document.getElementById('debt_velocity');
            const trendEl = document.getElementById('trend_direction');
            const countEl = document.getElementById('evo-commits-count');
            
            if (data.debt_velocity !== undefined && data.debt_velocity !== null) {
                velEl.textContent = (data.debt_velocity > 0 ? '+' : '') + data.debt_velocity.toFixed(4);
                velEl.style.color = data.debt_velocity > 0 ? 'var(--danger-color)' : (data.debt_velocity < 0 ? 'var(--success-color)' : 'var(--text-primary)');
            }
            if (data.trend_direction) {
                trendEl.textContent = data.trend_direction.toUpperCase();
            }
            if (data.commits_analyzed) {
                countEl.textContent = data.commits_analyzed;
            }
            if (data.snapshots && data.snapshots.length > 0) {
                updateEvolutionChart(data.snapshots);
            }
        }
        
        function updateEvolutionChart(snapshots) {
            if (!snapshots || snapshots.length === 0) {
                const ctx = document.getElementById('evolutionChart');
                if(ctx && ctx.parentElement) ctx.parentElement.innerHTML = getEmptyStateHtml('🕓', 'No Evolution Data', 'Run a Git history analysis to see the evolution chart.');
                return;
            }
            const labels = snapshots.map(s => {
                const d = new Date(s.committed_at);
                return `${d.getMonth()+1}/${d.getDate()} ${s.sha.substring(0, 7)}`;
            });
            const data = snapshots.map(s => s.health_score);
            
            if (evolutionChartInstance) {
                evolutionChartInstance.data.labels = labels;
                evolutionChartInstance.data.datasets[0].data = data;
                evolutionChartInstance.update();
                return;
            }
            
            const ctx = document.getElementById('evolutionChart').getContext('2d');
            const gradient = ctx.createLinearGradient(0, 0, 0, 400);
            gradient.addColorStop(0, 'rgba(16, 185, 129, 0.5)');
            gradient.addColorStop(1, 'rgba(16, 185, 129, 0.0)');
            
            evolutionChartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Commit Health Score',
                        data: data,
                        borderColor: '#10b981',
                        backgroundColor: gradient,
                        borderWidth: 2,
                        fill: true,
                        tension: 0.1,
                        pointBackgroundColor: '#0f172a',
                        pointBorderColor: '#10b981',
                        pointBorderWidth: 2,
                        pointRadius: 4,
                        pointHoverRadius: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: { legend: { display: false } },
                    scales: {
                        y: { beginAtZero: false, min: 0, max: 100, grid: { color: 'rgba(255, 255, 255, 0.05)' } },
                        x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } }
                    }
                }
            });
        }

        // Fetch initial data
        fetchData();
        setInterval(fetchData, 30000); // 30 seconds

        // ─── Advisor Panel (Step 11/12 – Anthropic Streaming) ───
        async function sendAdvisorQuestion() {
            const askBtn = document.getElementById('btn-advisor-ask');
            if (askBtn.disabled) return;  // already in flight -- ignore a duplicate click or Enter-key repeat

            const input = document.getElementById('advisor-question-input');
            const responseEl = document.getElementById('advisor-response');
            const question = input.value.trim();
            if (!question) return;

            responseEl.textContent = '▌';  // blinking cursor placeholder
            input.value = '';
            input.disabled = true;
            askBtn.disabled = true;

            try {
                const res = await fetch(`/api/v1/advisor/ask${jobQuery}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question })
                });

                if (!res.ok) {
                    responseEl.innerHTML = getErrorStateHtml(`Error ${res.status}: ${res.statusText}`, () => sendAdvisorQuestion());
                    return;
                }

                // Consume the SSE stream chunk-by-chunk
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let accumulated = '';
                let buffer = '';

                responseEl.textContent = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    const raw = decoder.decode(value, { stream: true });
                    // Each SSE event is "data: <text>\n\n"
                    buffer += raw;
                    const lines = buffer.split('\n');
                    buffer = lines.pop(); // last element may be an incomplete line -- carry it over
                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const chunk = line.slice(6);  // strip "data: "
                            accumulated += chunk;
                            responseEl.textContent = accumulated + '▌';
                        }
                    }
                }

                // Remove trailing cursor
                responseEl.textContent = accumulated || 'No response received.';

            } catch (err) {
                console.error('Advisor streaming error:', err);
                responseEl.innerHTML = getErrorStateHtml('Error communicating with AI Advisor.', () => sendAdvisorQuestion());
            } finally {
                input.disabled = false;
                askBtn.disabled = false;
                input.focus();
            }
        }

        // ─── Remediation Panel (Step 17) ───

        async function generateRemediationPlan() {
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

        function renderRemediationTasks(tasks, targetEl) {
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
                    </div>
                `;
            }).join('');
        }

        async function generateViolationsRemediation() {
            const btn = document.getElementById('violations-remediation-btn');
            const resultsEl = document.getElementById('violations-remediation-results');

            if (!window.latestRun || !window.latestRun.violations || window.latestRun.violations.length === 0) {
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
                    window.remediationSelectedKeys = new Set(data.selection.selected_keys);
                    updateViolationsTable(window.latestRun);
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
        
        async function loadRepoHistory(repoUrl) {
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

        function renderRepoHistoryTable(runs, tableEl) {
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

        // ─── Run comparison (Phase 6.5) ───
        // Populate the recent-analyses pickers and let the user diff two runs.
        function _runLabel(r) {
            const d = r.timestamp ? new Date(r.timestamp) : null;
            const when = d ? `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2,'0')}` : '?';
            return `${r.score ?? '--'} (${r.band ?? '?'}) · ${when} · ${(r.repo_url||'').replace(/^https?:\/\/github\.com\//,'').replace(/\.git$/,'')}`;
        }

        function populateRecentPicker(runs) {
            window.recentRuns = runs || [];
            const pickers = [
                document.getElementById('recent-analyses-picker'),
                document.getElementById('compare-picker'),
            ];
            pickers.forEach(p => {
                if (!p) return;
                const placeholder = p.querySelector('option')?.textContent || '';
                p.innerHTML = `<option value="">${placeholder}</option>`;
                for (const r of window.recentRuns) {
                    if (!r.job_id) continue;
                    const opt = document.createElement('option');
                    opt.value = r.job_id;
                    opt.textContent = _runLabel(r);
                    p.appendChild(opt);
                }
            });
            // Pre-select the current highlighted run in the base picker.
            if (highlightJobId) {
                const base = document.getElementById('recent-analyses-picker');
                if (base) base.value = highlightJobId;
            }
        }

        function _violationKey(v) {
            // Match key used by the suppression store: module + layer + message.
            return `${v.module||''}|${v.layer||''}|${v.message||''}`;
        }

        async function _fetchRunByJobId(jobId) {
            // /api/v1/runs?job_id=... returns all entries for that job; take the newest.
            const res = await fetch(`/api/v1/runs?limit=50&job_id=${jobId}`);
            if (!res.ok) return null;
            const data = await res.json();
            const rs = data.runs || [];
            return rs.length ? rs[rs.length - 1] : null;
        }

        async function renderRunComparison(jobIdA, jobIdB) {
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
                const va = new Map((a.violations || []).map(v => [_violationKey(v), v]));
                const vb = new Map((b.violations || []).map(v => [_violationKey(v), v]));
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

        function initCompareControls() {
            const toggle = document.getElementById('compare-toggle-btn');
            const comparePicker = document.getElementById('compare-picker');
            const basePicker = document.getElementById('recent-analyses-picker');
            // Disable compare when fewer than 2 runs are available
            if (toggle && basePicker && basePicker.options.length < 2) {
                toggle.disabled = true;
                toggle.title = 'Need at least 2 analyses to compare';
            }
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
// Suppression UI logic
async function loadSuppressions() {
    const tbody = document.querySelector('#suppressionsTable tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" class="text-helper-center">Loading...</td></tr>';
    
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
    }
}

async function removeSuppression(id) {
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

async function createSuppression(module, layer, message) {
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
// Module Blast Radius UI
async function analyzePRRisk() {
    const btn = document.getElementById('analyze-risk-btn');
    const resultsDiv = document.getElementById('risk-results');

    if (btn) btn.disabled = true;
    if (btn) btn.textContent = 'Analyzing...';
    resultsDiv.innerHTML = '<div class="text-helper">Computing module blast radius...</div>';

    try {
        const urlParams = new URLSearchParams(window.location.search);
        const jobId = urlParams.get('job_id');
        const q = jobId ? `?job_id=${jobId}` : '';
        const res = await fetch(`/api/v1/risk${q}`);

        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            resultsDiv.innerHTML = getErrorStateHtml(body.detail || `HTTP ${res.status}`, () => analyzePRRisk());
            return;
        }
        const data = await res.json();

        const isCritical = data.level === 'critical';
        const isHigh = data.level === 'high';
        const levelColor = isCritical ? 'var(--danger-color)' : (isHigh ? 'var(--warn-color)' : (data.level === 'none' ? 'var(--text-secondary)' : 'var(--success-color)'));

        const modules = data.modules || [];
        const top = modules.slice(0, 15);

        let html = `
            <div style="margin-top: 1rem;">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:0.5rem;">
                    <div style="font-size: 1.25rem; font-weight: bold; color: ${levelColor};">Blast Radius: ${sanitize(data.level).toUpperCase()}</div>
                    <div class="text-helper" style="font-size: 0.85em">Modules analyzed: ${modules.length} · Widest reach: ${data.max_downstream ?? 0} downstream</div>
                </div>
                <p class="section-note" style="margin-top:0.5rem">For each module, the count of other modules that transitively depend on it — i.e. how far a change to it would ripple. Hotspots (≥ ${data.threshold ?? 3}) are flagged.</p>`;

        if (top.length === 0) {
            html += getEmptyStateHtml('🧩', 'No Modules', 'No modules were found in this repository\'s contract.');
        } else {
            const rows = top.map(m => {
                const isHot = m.downstream >= (data.threshold ?? 3);
                const tag = isHot
                    ? '<span class="badge badge-critical" style="margin-left:0.5rem;">hotspot</span>'
                    : '';
                const barW = data.max_downstream ? Math.round((m.downstream / data.max_downstream) * 100) : 0;
                return `
                    <div class="compare-diff-row">
                        <span style="flex:1;">${sanitize(m.module)}${tag}</span>
                        <span class="text-helper" style="min-width:2.5rem; text-align:right;">${m.downstream}</span>
                        <span style="flex:1.5; background:rgba(255,255,255,0.05); border-radius:4px; height:6px; overflow:hidden;">
                            <span style="display:block; height:100%; width:${barW}%; background:${isHot ? 'var(--danger-color)' : 'var(--accent-color)'};"></span>
                        </span>
                    </div>`;
            }).join('');
            html += `<div class="compare-diff" style="margin-top:0.5rem;">${rows}</div>`;
            if (modules.length > 15) {
                html += `<div class="text-helper" style="margin-top:0.5rem;">Showing the 15 widest-reach modules of ${modules.length}.</div>`;
            }
        }

        html += `</div>`;
        resultsDiv.innerHTML = html;

    } catch (e) {
        resultsDiv.innerHTML = getErrorStateHtml('Failed to compute blast radius: ' + e.message, () => analyzePRRisk());
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Analyze Blast Radius';
        }
    }
}
