/**
 * Talking to the dashboard's own API.
 *
 * The job id lives in the query string on purpose: it makes a result page
 * bookmarkable and survivable across a refresh. Every read is scoped to it, so
 * the two suffixes are derived once here rather than rebuilt at each of the
 * dozen call sites that used to interpolate them by hand.
 */

const params = new URLSearchParams(window.location.search);

/** The job this page is showing, or null when opened without one. */
export const highlightJobId = params.get('job_id');

/** `?job_id=...` for a bare endpoint, `&job_id=...` for one with a query. */
export const jobQuery = highlightJobId ? `?job_id=${highlightJobId}` : '';
export const jobQueryAmp = highlightJobId ? `&job_id=${highlightJobId}` : '';


export async function safeFetch(url, fallback) {
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
