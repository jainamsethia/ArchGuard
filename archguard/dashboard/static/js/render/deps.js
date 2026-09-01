import { state } from '../state.js';
import { jobQuery } from '../api.js';
import { getEmptyStateHtml, getErrorStateHtml, sanitize } from '../dom.js';


/** One in-flight download, reused, so rapid tab switching fetches 628 KB once. */
let visLoader = null;


export function loadGraphLibrary() {
    if (window.vis) return Promise.resolve();
    if (visLoader) return visLoader;
    visLoader = new Promise((resolve, reject) => {
        const tag = document.createElement('script');
        // No nonce needed: the CSP is `script-src 'self' 'nonce-...'`,
        // and source lists are additive, so a same-origin script is
        // already allowed. Adding one would be machinery for nothing.
        tag.src = '/vendor/vis-network.min.js';
        tag.onload = () => resolve();
        tag.onerror = () => {
            visLoader = null;
            reject(new Error('Could not load the dependency graph library'));
        };
        document.head.appendChild(tag);
    });
    return visLoader;
}


export async function initDependencyGraph() {
    if (state.visNetwork) {
        state.visNetwork.redraw();
        state.visNetwork.fit();
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
    state.visNetwork = new vis.Network(container, data, options);
}


export async function scanDependencies() {
    const btn = document.getElementById('scan-deps-btn');
    const statusEl = document.getElementById('deps-status');
    const scoreEl = document.getElementById('deps-score');
    const tableContainer = document.getElementById('deps-table-container');
    const tbody = document.querySelector('#depsTable tbody');

    // The scan itself ran in the worker, during the analysis, while the clone
    // still existed. This reads the result. Saying "running pip-audit" here
    // would describe work that finished minutes ago -- and, before the scan
    // moved, described work the web process could not do at all.
    btn.disabled = true;
    btn.textContent = "Loading…";
    statusEl.textContent = "Loading the dependency scan for this analysis…";

    try {
        const res = await fetch(`/api/v1/deps${jobQuery}`);
        if (!res.ok) {
            // The same three categories the rest of the dashboard uses, rather
            // than a status code. "Error 401" tells a reader nothing they can
            // act on; "sign in again" does, and a bare number is the sort of
            // thing that ends up quoted in a support thread instead of fixed.
            statusEl.textContent =
                res.status === 401 || res.status === 403
                    ? 'Your session has expired. Sign in again to see the dependency scan.'
                    : res.status === 429
                        ? 'Too many requests. Try again shortly.'
                        : "We couldn't load the dependency scan. Try again.";
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
        btn.textContent = "Show dependency scan";
    }
}


export async function analyzePRRisk() {
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
