/**
 * Telling the reader that the data on screen is not the data.
 *
 * Every request failure used to end as `--` in whichever slots were still
 * loading, with the reason in a `title`. That made a rate limit look like a
 * server fault, both look like a repository with no analyses, and none of them
 * look like anything a person could act on.
 *
 * An expired session is not handled here. The page already has an
 * authentication UI -- the sign-in overlay auth.js drives -- and a second one
 * competing with it would be worse than the problem. `api.js` announces the
 * failure and auth.js raises the overlay it already owns.
 *
 * What is left is the three the reader can only wait on or retry, plus the
 * thing they all share: whatever is currently on screen was not refreshed, and
 * saying so is the whole point.
 */

import { FAILURE } from '../api.js';

/**
 * One sentence per category, and no server text in any of them.
 *
 * A backend exception carries paths, table names and query fragments. None of
 * it helps someone reading a dashboard, and it is exactly what an attacker
 * probing an endpoint would like to be shown. Detail goes to the console.
 */
const MESSAGES = {
    [FAILURE.RATE_LIMIT]: {
        title: 'Too many requests',
        body: 'This dashboard is refreshing faster than the server allows.',
    },
    [FAILURE.SERVER]: {
        title: "We couldn't load this data",
        body: 'The server did not answer correctly. Nothing on this page has changed.',
    },
    [FAILURE.NETWORK]: {
        title: "We couldn't reach the server",
        body: 'Check your connection. Nothing on this page has changed.',
    },
    [FAILURE.DATA]: {
        title: "We couldn't read the response",
        body: 'The server answered with something this page did not understand.',
    },
};


function panel() {
    let el = document.getElementById('api-status');
    if (el) return el;

    const container = document.getElementById('dashboard-main')
        || document.querySelector('.dashboard-container');
    if (!container) return null;

    el = document.createElement('div');
    el.id = 'api-status';
    el.className = 'api-status';
    // `status`, not `alert`: this interrupts nothing, and a polite live region
    // lets a screen reader finish its sentence before announcing it. `hidden`
    // rather than display:none via a class so the element is out of the
    // accessibility tree entirely when there is nothing wrong.
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.hidden = true;
    container.insertBefore(el, container.firstChild);
    return el;
}


/**
 * Show what went wrong, and mark the data underneath as not current.
 *
 * `data-stale` is what stops the previous poll's numbers reading as this
 * poll's. They stay on screen deliberately -- last-known values are useful and
 * blanking a dashboard on one failed refresh is its own kind of lie -- but they
 * are dimmed, and the banner above them says they were not updated.
 */
export function renderApiFailure(failure, { onRetry } = {}) {
    if (!failure || failure.kind === FAILURE.AUTH) return;

    const el = panel();
    if (!el) return;

    const copy = MESSAGES[failure.kind] || MESSAGES[FAILURE.SERVER];
    const wait = failure.retryAfter
        ? ` Try again in about ${failure.retryAfter} seconds.`
        : ' Try again shortly.';

    el.innerHTML = '';

    const heading = document.createElement('strong');
    heading.className = 'api-status-title';
    heading.textContent = copy.title;

    const body = document.createElement('p');
    body.className = 'api-status-body';
    body.textContent =
        copy.body + (failure.kind === FAILURE.RATE_LIMIT ? wait : ' Try again.');

    el.append(heading, body);

    if (onRetry) {
        // A real button: reachable by Tab, activated by Enter and Space, and
        // announced as a control. A clickable div would be none of those.
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.id = 'api-status-retry';
        retry.className = 'btn-secondary api-status-retry';
        retry.textContent = 'Try again';
        retry.addEventListener('click', onRetry);
        el.append(retry);
    }

    el.hidden = false;
    markStale(true);
}


export function clearApiFailure() {
    const el = document.getElementById('api-status');
    if (el) {
        el.hidden = true;
        el.innerHTML = '';
    }
    markStale(false);
}


function markStale(stale) {
    const container = document.getElementById('dashboard-main')
        || document.querySelector('.dashboard-container');
    if (!container) return;
    if (stale) {
        container.setAttribute('data-stale', 'true');
    } else {
        container.removeAttribute('data-stale');
    }
}
