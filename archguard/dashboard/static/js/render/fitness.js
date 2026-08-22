import { getEmptyStateHtml, sanitize } from '../dom.js';


export function updateFitnessPanel(latestRun) {
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
