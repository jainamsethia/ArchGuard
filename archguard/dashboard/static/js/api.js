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


/**
 * Why a request did not produce data.
 *
 * These are the categories the user can act on, and they are deliberately
 * coarse: "sign in again", "wait", "try again". The status code and the
 * server's text stay in the console, because a backend exception string
 * carries paths, module names and query fragments that a visitor should never
 * be shown and cannot do anything with.
 */
export const FAILURE = {
    AUTH: 'auth',
    RATE_LIMIT: 'rate-limit',
    SERVER: 'server',
    NETWORK: 'network',
    DATA: 'data',
};

/**
 * Worst-first. Five requests go out together and any of them can fail
 * differently; the page shows one thing, so it has to be the one that explains
 * the rest. A 401 makes every other request in the same cycle fail too, so
 * reporting the 500 behind it would describe a symptom.
 */
const SEVERITY = [FAILURE.AUTH, FAILURE.RATE_LIMIT, FAILURE.SERVER, FAILURE.NETWORK, FAILURE.DATA];

let currentFailure = null;

/** Forget the previous cycle. Without this one blip is reported forever. */
export function beginFetchCycle() {
    currentFailure = null;
}

/** The worst failure recorded since `beginFetchCycle`, or null. */
export function lastApiFailure() {
    return currentFailure;
}

function record(failure) {
    const better =
        currentFailure === null ||
        SEVERITY.indexOf(failure.kind) < SEVERITY.indexOf(currentFailure.kind);
    if (better) currentFailure = failure;

    // Announced as well as recorded, so the sign-in overlay can react to an
    // expired session without the polling loop having to know about it.
    //
    // Wrapped, and every part of it optional. This is the notification step of
    // an error path: if dispatching throws -- no CustomEvent constructor, a
    // listener that raises -- the caller must still get its fallback. A failure
    // to report a failure is not a reason to turn a handled one into an
    // unhandled one.
    try {
        const Ctor = typeof CustomEvent !== 'undefined'
            ? CustomEvent
            : (typeof window !== 'undefined' ? window.CustomEvent : null);
        if (Ctor && typeof window !== 'undefined' && window.dispatchEvent) {
            window.dispatchEvent(new Ctor('archguard:apifailure', { detail: failure }));
        }
    } catch (e) {
        console.warn('[dashboard] could not announce the API failure:', e.message);
    }
}

/**
 * `Retry-After` in seconds, or null.
 *
 * The header may also be an HTTP date, which is legal and which we do not
 * parse. Falling back to the caller's own default is better than guessing at a
 * number the schedule then depends on.
 */
function retryAfterSeconds(resp) {
    const raw = resp.headers && resp.headers.get ? resp.headers.get('Retry-After') : null;
    if (!raw) return null;
    const seconds = Number.parseInt(raw, 10);
    return Number.isFinite(seconds) && String(seconds) === String(raw).trim() && seconds >= 0
        ? seconds
        : null;
}

function classify(status) {
    if (status === 401 || status === 403) return FAILURE.AUTH;
    if (status === 429) return FAILURE.RATE_LIMIT;
    return FAILURE.SERVER;
}

/**
 * Fetch JSON, returning the fallback and classifying anything that goes wrong.
 *
 * The signature is unchanged on purpose: a dozen call sites destructure the
 * result and none of them should have to learn about error handling. What is
 * new is that the failure is recorded rather than flattened into `--` with the
 * reason hidden in a `title` -- which made a signed-out session, a rate limit,
 * a server fault, a dropped connection and a genuinely empty repository all
 * look the same on screen.
 */
export async function safeFetch(url, fallback) {
    let resp;
    try {
        resp = await fetch(url);
    } catch (e) {
        console.warn(`[dashboard] ${url} could not be reached:`, e.message);
        record({ kind: FAILURE.NETWORK, status: 0, retryAfter: null });
        return fallback;
    }

    if (!resp.ok) {
        console.warn(`[dashboard] ${url} answered HTTP ${resp.status}`);
        record({
            kind: classify(resp.status),
            status: resp.status,
            retryAfter: resp.status === 429 ? retryAfterSeconds(resp) : null,
        });
        return fallback;
    }

    try {
        return await resp.json();
    } catch (e) {
        // A 200 whose body is not JSON is a broken response, and returning the
        // fallback quietly would report it as an empty repository.
        console.warn(`[dashboard] ${url} returned a body that is not JSON:`, e.message);
        record({ kind: FAILURE.DATA, status: resp.status, retryAfter: null });
        return fallback;
    }
}
