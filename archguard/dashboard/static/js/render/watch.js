/**
 * The watched-repository card.
 *
 * Deliberately small. Watching is one boolean, one number and one optional
 * URL, and the value is in the alert arriving -- not in the configuration
 * screen. A notification-preferences matrix here would be more code than the
 * feature it configures.
 *
 * The webhook field is write-only: the API returns whether one is set, never
 * what it is, because a webhook URL routinely carries a token in its path.
 *
 * Three controls, because they do three different things to the row:
 *
 *   Pause / Resume   PATCH {active}      keeps threshold, webhook, alert history
 *   Save changes     PATCH {threshold}   changes configuration, keeps watching
 *   Remove watch     DELETE              deletes the row and everything on it
 *
 * That separation is the point. Resuming used to go through POST, which is an
 * upsert that overwrites `webhook_url` and `health_drop_threshold` with
 * whatever the form happened to hold -- so pausing a watch and resuming it
 * silently cleared its webhook and reset its threshold to 5.
 */

import { CONFLICT, safeFetch, sendJson } from '../api.js';
import { state } from '../state.js';

/** The watch for the repository this page is showing, or null. */
let current = null;
//: The watch state could not be read. Distinct from `current === null`, which
//: is the answer "this repository is not watched".
let unknown = false;
//: The repository `current` describes. Kept because `current` outliving its
//: repository is how the card comes to show one project's watch under
//: another's -- or under no project at all.
let currentUrl = null;

//: Identity-compared, so a failed fetch is distinguishable from a successful
//: one that happens to return nothing.
const FAILED = Object.freeze({ failed: true });

//: The server's own default, from WatchRequest.health_drop_threshold. Used
//: when creating a watch with the field left blank, so the number the UI sends
//: is the number the API documents rather than a second opinion.
const DEFAULT_THRESHOLD = 5.0;

function repoUrl() {
    return state.latestRun && state.latestRun.repo_url;
}

function el(id) {
    return document.getElementById(id);
}

/**
 * Forget the watch on screen, because it is not this page's any more.
 *
 * Deliberately not called on every render. Clearing unconditionally would wipe
 * the threshold field on each thirty-second poll, which is the defect this
 * sits next to rather than a second copy of it.
 */
function forget() {
    current = null;
    unknown = false;
    currentUrl = null;
    const threshold = el('watch-threshold');
    const webhook = el('watch-webhook');
    if (threshold) { threshold.value = ''; threshold.disabled = false; }
    if (webhook) { webhook.value = ''; webhook.disabled = false; }
    const save = el('watch-save-btn');
    const remove = el('watch-unwatch-btn');
    if (save) save.hidden = true;
    if (remove) remove.hidden = true;
    setStatus('Loading…');
}

export async function loadWatchState() {
    const url = repoUrl();
    const card = el('watch-card');
    if (!card) return;

    if (!url) {
        // The page is showing a run that names no repository. Returning here
        // used to leave the card visible with the previous project's status,
        // threshold and Remove button -- a configuration screen for something
        // that is not on screen, whose delete button pointed at a real watch.
        forget();
        card.hidden = true;
        return;
    }

    if (url !== currentUrl) {
        // A different repository. Whatever is on the card describes the last
        // one, and it must not be shown under this one's name while the
        // request is in flight.
        forget();
        currentUrl = url;
    }

    card.hidden = false;

    // Through safeFetch rather than a bare fetch, so an expired session raises
    // the sign-in overlay and a rate limit is reported as one, instead of this
    // card inventing its own private "something went wrong".
    const data = await safeFetch('/api/v1/watch', FAILED);
    if (data === FAILED) {
        // Not `current = null`. That renders as "Not watched", which is a
        // claim about the account this request just failed to read -- and the
        // button underneath it offers to start watching something that may
        // already be watched.
        unknown = true;
    } else {
        unknown = false;
        current = (data.watched || []).find(w => w.repo_url === url) || null;
    }
    renderWatchState();
}

/**
 * Write the saved threshold into the input, unless the user is editing it.
 *
 * `loadWatchState` runs on every thirty-second poll, so an unconditional write
 * would delete what someone is halfway through typing.
 */
function fillThreshold() {
    const input = el('watch-threshold');
    if (!input || input === document.activeElement) return;
    input.value =
        current && current.health_drop_threshold != null
            ? String(current.health_drop_threshold)
            : '';
}

function renderWatchState() {
    const status = el('watch-status');
    const toggle = el('watch-toggle-btn');
    const save = el('watch-save-btn');
    const remove = el('watch-unwatch-btn');
    if (!status || !toggle) return;

    const configuring = [el('watch-threshold'), el('watch-webhook')];

    if (unknown) {
        // Everything off. A card that has just admitted it does not know
        // whether this repository is watched must not offer to change it: that
        // is how a watch gets created twice, or removed by someone who thought
        // they were creating one.
        status.textContent =
            "We couldn't check whether this repository is being watched. "
            + 'It will try again on the next refresh.';
        toggle.disabled = true;
        configuring.forEach(input => { if (input) input.disabled = true; });
        // The label is left alone deliberately. Relabelling to "Watch this
        // repository" would contradict the last thing known to be true.
        if (save) save.hidden = true;
        if (remove) remove.hidden = true;
        return;
    }

    toggle.disabled = false;
    configuring.forEach(input => { if (input) input.disabled = false; });
    fillThreshold();

    if (!current) {
        status.textContent =
            'Not watched. Turn this on to be told when the architecture gets worse.';
        toggle.textContent = 'Watch this repository';
        if (save) save.hidden = true;
        if (remove) remove.hidden = true;
        return;
    }

    // Watched or paused: both are configured, so both can be edited and both
    // can be removed.
    if (save) save.hidden = false;
    if (remove) remove.hidden = false;
    toggle.textContent = current.active ? 'Pause watching' : 'Resume watching';
    status.textContent = describe(current);
}

/**
 * What is known about this watch, in the order someone would ask.
 *
 * Only fields the API actually returns, and only claims they actually support.
 * There is no "next scan" here because nothing in the backend records one --
 * the schedule lives in two worker constants and whether the worker is running
 * at all -- and a countdown derived from those would be a guess presented as a
 * fact.
 */
function describe(watch) {
    const parts = [];
    parts.push(
        watch.active
            ? 'Watched. Rescanned daily.'
            : 'Paused. No scheduled scans are running.',
    );
    if (watch.health_drop_threshold != null) {
        parts.push(`Alerting on a drop of more than ${watch.health_drop_threshold}.`);
    }
    parts.push(
        watch.has_webhook
            ? 'Regressions are sent to your webhook.'
            : 'Regressions are recorded here; no webhook is set.',
    );
    if (watch.last_checked_at) {
        parts.push(`Last checked ${new Date(watch.last_checked_at).toLocaleString()}.`);
    } else {
        parts.push('Not scanned yet.');
    }
    // Verbatim, and assigned to textContent below rather than innerHTML, which
    // is what makes it safe. Running it through an HTML escaper first would
    // double-escape a gate named "API & Web" into "API &amp; Web".
    if (watch.last_status) parts.push(watch.last_status);
    return parts.join(' ');
}


/** Start watching, or pause/resume an existing watch. */
export async function toggleWatch() {
    const url = repoUrl();
    if (!url || unknown) return;

    await withButtons(async () => {
        if (current) {
            // PATCH, never POST. POST is an upsert that overwrites the webhook
            // and the threshold with whatever the form holds, so resuming
            // through it wiped both.
            const body = await sendJson(`/api/v1/watch/${current.id}`, 'PATCH', {
                active: !current.active,
            });
            current = body.watched;
        } else {
            const threshold = readThreshold();
            if (threshold === null) return;
            const webhook = (el('watch-webhook').value || '').trim();
            const body = await sendJson('/api/v1/watch', 'POST', {
                repo_url: url,
                health_drop_threshold: threshold,
                webhook_url: webhook || null,
            }, {
                // The card only POSTs when it believes nothing is watched, so a
                // conflict means its picture is stale -- watched in another tab,
                // or on another device. Re-reading is the answer; reporting
                // "Could not update" would be describing our own staleness as
                // the user's problem.
                onConflict: async () => {
                    await loadWatchState();
                    setStatus(
                        'This repository is already being watched. '
                        + 'Showing its current settings.',
                    );
                },
            });
            if (body === CONFLICT) return;
            current = body.watched;
            // Never echo it back into the field: the server does not return it,
            // and leaving it on screen is the only copy still visible.
            el('watch-webhook').value = '';
        }
        renderWatchState();
    });
}


/** Persist an edited threshold, and an added webhook if one was typed. */
export async function saveWatchSettings() {
    if (!current || unknown) return;

    const threshold = readThreshold({ required: true });
    if (threshold === null) return;

    await withButtons(async () => {
        const webhook = (el('watch-webhook').value || '').trim();
        const patch = { health_drop_threshold: threshold };
        // Omitted rather than sent as null when blank: the API reads null as
        // "leave unchanged", so sending it for an untouched field is both
        // pointless and, if that ever changed, destructive.
        if (webhook) patch.webhook_url = webhook;

        const body = await sendJson(`/api/v1/watch/${current.id}`, 'PATCH', patch);
        // Only now. The input showed what the user typed; this is what the
        // server stored, and until it answered they were not the same thing.
        current = body.watched;
        if (webhook) el('watch-webhook').value = '';
        renderWatchState();
    });
}


/** Delete the watch. Not a pause -- this is the row and its history going. */
export async function unwatch() {
    if (!current || unknown) return;

    const confirmed = window.confirm(
        'Remove this watch?\n\n'
        + 'Scheduled scans stop, and the threshold, webhook and alert history '
        + 'are deleted. To stop scans temporarily and keep the settings, use '
        + 'Pause instead.',
    );
    if (!confirmed) return;

    await withButtons(async () => {
        // A real request. Clearing `current` without one would show "Not
        // watched" over a row that is still being scanned every night.
        await sendJson(`/api/v1/watch/${current.id}`, 'DELETE');
        // Only after the server confirmed. 204 carries no body, so there is
        // nothing to re-read -- the transition is the local state going.
        current = null;
        el('watch-webhook').value = '';
        renderWatchState();
    });
}


/**
 * The threshold the user typed, or null if it is not usable.
 *
 * `parseFloat(x) || 5.0` turned an empty box, `abc` and `0` all into 5.0 with
 * no feedback, and sent `0.1` straight at a field the API declares `ge=0.5` --
 * which comes back a 422 whose `detail` is a list, rendered as
 * "Could not update: [object Object]". The input already carries min, max and
 * step, so the browser can say what is wrong in the user's own language.
 */
function readThreshold({ required = false } = {}) {
    const input = el('watch-threshold');
    if (!input) return DEFAULT_THRESHOLD;

    const raw = (input.value || '').trim();
    if (!raw) {
        if (!required) return DEFAULT_THRESHOLD;
        setStatus('Enter a threshold, or leave the field alone to keep the current one.');
        return null;
    }
    if (input.checkValidity && !input.checkValidity()) {
        if (input.reportValidity) input.reportValidity();
        setStatus(
            `Threshold must be a number between ${input.min || '0.5'} and ${input.max || '100'}.`,
        );
        return null;
    }
    const value = Number.parseFloat(raw);
    if (!Number.isFinite(value)) {
        setStatus('Threshold must be a number.');
        return null;
    }
    return value;
}


/**
 * Run a mutation with every control disabled, and put the reason on screen if
 * it fails.
 *
 * The message survives only until the next poll rewrites the status line,
 * which is why a rejected webhook is also reported through the browser's own
 * validity UI where the field can hold it.
 */
async function withButtons(work) {
    const buttons = ['watch-toggle-btn', 'watch-save-btn', 'watch-unwatch-btn']
        .map(el)
        .filter(Boolean);
    buttons.forEach(b => { b.disabled = true; });
    try {
        await work();
    } catch (e) {
        // The message matters here -- a rejected webhook URL is the common case
        // and the user needs to know which part was refused.
        setStatus(`Could not update: ${e.message}`);
    } finally {
        // Not blanket-enabled: `unknown` may have become true while this ran,
        // and re-enabling then would undo the one thing that state is for.
        buttons.forEach(b => { b.disabled = unknown; });
    }
}


function setStatus(text) {
    const status = el('watch-status');
    if (status) status.textContent = text;
}
