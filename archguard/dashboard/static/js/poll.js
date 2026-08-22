import { state } from './state.js';
import { highlightJobId, jobQuery, jobQueryAmp, safeFetch } from './api.js';
import { sanitize } from './dom.js';
import { updateModuleChart, updateTrendChart } from './render/charts.js';
import { populateRecentPicker } from './render/compare.js';
import { _applyGitEvolutionData, updateEvolutionTrends } from './render/evolution.js';
import { updateFitnessPanel } from './render/fitness.js';
import { clearEmptyState, hideRefreshLoader, renderEmptyState, updateLayerStatus, updateMetrics, updateProvenanceBanner, updateThresholdNote, updateViolationCounts } from './render/metrics.js';
import { updateViolationsTable } from './render/violations.js';


// Polling is a handle, not a fire-and-forget interval, so the empty state can
// stop it and a recovered dashboard can start it again.
let pollTimer = null;
let pollingWanted = false;


export function startPolling() {
    pollingWanted = true;
    // A hidden tab gets nothing: five endpoints every thirty seconds,
    // forever, in a background tab nobody is looking at, is work the
    // server does for no reader.
    if (document.hidden) return;
    if (pollTimer === null) pollTimer = setInterval(fetchData, 30000);
}


export function stopPolling() {
    pollingWanted = false;
    suspendPolling();
}


export function suspendPolling() {
    if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
    }
}


export function runIsTerminal(run) {
    const status = String((run && run.status) || '').toLowerCase();
    return status === 'complete' || status === 'failed';
}


export async function fetchData() {
    const refreshLoader = document.getElementById('refresh-loader');
    if (refreshLoader) refreshLoader.style.display = 'inline-block';
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
                    banner.style.background = 'var(--tint-success-bg)';
                    banner.style.borderColor = 'var(--success-color)';

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

        // Data is present: undo any earlier empty state and make sure
        // the poll is running again.
        clearEmptyState();
        startPolling();

        updateProvenanceBanner(latestData);
        updateThresholdNote(latestData);
        updateLayerStatus(latestData);

        if (latestData) {
            state.latestRun = latestData;
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
        hideRefreshLoader();
    }
}


/**
 * Suspend polling while the tab is hidden, and catch up when it returns.
 *
 * Exported and called by main rather than registered on import: a module that
 * attaches a document listener the moment it is imported cannot be imported by
 * a test without side effects, which is half the reason the old single-script
 * page was hard to test at all.
 */
export function initVisibilityPolling() {
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            suspendPolling();
        } else if (pollingWanted) {
            // Catch up immediately rather than waiting out a fresh interval,
            // so a returning tab is not showing stale numbers.
            fetchData();
            startPolling();
        }
    });
}
