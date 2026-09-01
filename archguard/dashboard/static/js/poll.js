import { state } from './state.js';
import { FAILURE, beginFetchCycle, highlightJobId, jobQuery, jobQueryAmp, lastApiFailure, safeFetch } from './api.js';
import { sanitize } from './dom.js';
import { updateModuleChart, updateTrendChart } from './render/charts.js';
import { populateRecentPicker } from './render/compare.js';
import { _applyGitEvolutionData, updateEvolutionTrends } from './render/evolution.js';
import { updateFitnessPanel } from './render/fitness.js';
import { clearEmptyState, hideRefreshLoader, renderEmptyState, updateLayerStatus, updateMetrics, updateProvenanceBanner, updateThresholdNote, updateViolationCounts } from './render/metrics.js';
import { clearApiFailure, renderApiFailure } from './render/status.js';
import { loadWatchState } from './render/watch.js';
import { updateViolationsTable } from './render/violations.js';


//: How long to wait before trying again when the server rate-limited us and
//: did not say for how long. The server's window is 60s, so this matches it
//: rather than guessing lower and being refused again.
const DEFAULT_BACKOFF_SECONDS = 60;

//: The pending backoff, so a second failure does not stack a second timer.
let backoffTimer = null;


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


/**
 * React to a request that did not produce data.
 *
 * Three different answers, because they need three different things from the
 * reader. A dead session needs them to sign in and nothing else will help. A
 * rate limit needs them to wait, and needs us to stop asking -- polling every
 * thirty seconds into a 429 is what caused it. Everything else needs a way to
 * try again.
 */
function handleFailure(failure) {
    if (failure.kind === FAILURE.AUTH) {
        // The session is gone, so there is nothing to poll for and every
        // further request would be another 401. auth.js raises the sign-in
        // overlay in response to the event api.js already dispatched -- the
        // page's one authentication UI, rather than a second one here.
        stopPolling();
        return;
    }

    if (failure.kind === FAILURE.RATE_LIMIT) {
        // Stop asking. The window is what has to pass, and continuing to poll
        // through it keeps the budget spent and the dashboard refused.
        suspendPolling();
        const wait = (failure.retryAfter ?? DEFAULT_BACKOFF_SECONDS) * 1000;
        if (backoffTimer === null) {
            backoffTimer = setTimeout(() => {
                backoffTimer = null;
                if (pollingWanted) {
                    fetchData();
                    startPolling();
                }
            }, wait);
            // A pending timer keeps Node's event loop alive, so under the test
            // runner a single rate-limited case would hold the whole suite open
            // for the length of the backoff. No effect in a browser, where
            // timers do not keep anything alive.
            if (typeof backoffTimer === 'object' && backoffTimer?.unref) backoffTimer.unref();
        }
    }

    renderApiFailure(failure, { onRetry: fetchData });
}


export async function fetchData() {
    const refreshLoader = document.getElementById('refresh-loader');
    if (refreshLoader) refreshLoader.style.display = 'inline-block';

    // A spinner is invisible to a screen reader. aria-busy on the region being
    // rewritten is what tells assistive technology to hold off rather than
    // announce a half-updated table as five separate panels land.
    const region = document.getElementById('dashboard-main');
    if (region) region.setAttribute('aria-busy', 'true');

    try {
        beginFetchCycle();
        const [runsData, latestData, modulesData, evolutionData, gitEvoData] = await Promise.all([
            safeFetch(`/api/v1/runs?limit=30${jobQueryAmp}`, { runs: [] }),
            safeFetch(`/api/v1/runs/latest${jobQuery}`, null),
            safeFetch(`/api/v1/modules${jobQuery}`, { modules: [] }),
            safeFetch(`/api/v1/evolution/trends${jobQuery}`, { trends: [] }),
            safeFetch(`/api/v1/evolution/latest${jobQuery}`, null)
        ]);

        // Before the empty check, and that order is the fix. A failed request
        // returns its fallback -- an empty list, a null -- which is
        // indistinguishable from a repository that genuinely has no analyses.
        // So a signed-out session, a rate limit and a server fault all rendered
        // "No analyses yet. Analyze a repository." to someone whose analyses
        // were sitting there behind a 401.
        const failure = lastApiFailure();
        if (failure) {
            handleFailure(failure);
            return;
        }
        clearApiFailure();

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
            // After latestRun is set: the card is keyed by repo_url, which
            // only the run knows.
            loadWatchState();
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
        if (region) region.setAttribute('aria-busy', 'false');
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
