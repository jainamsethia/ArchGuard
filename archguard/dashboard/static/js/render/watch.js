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
 */

import { state } from '../state.js';
import { sanitize } from '../dom.js';

/** The watch for the repository this page is showing, or null. */
let current = null;

function repoUrl() {
    return state.latestRun && state.latestRun.repo_url;
}

function el(id) {
    return document.getElementById(id);
}

export async function loadWatchState() {
    const url = repoUrl();
    const card = el('watch-card');
    if (!card || !url) return;
    card.hidden = false;

    try {
        const res = await fetch('/api/v1/watch');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        current = (data.watched || []).find(w => w.repo_url === url) || null;
    } catch (e) {
        console.warn('[dashboard] could not load watch state:', e.message);
        current = null;
    }
    renderWatchState();
}

function renderWatchState() {
    const status = el('watch-status');
    const button = el('watch-toggle-btn');
    if (!status || !button) return;

    if (!current || !current.active) {
        status.textContent = current
            ? 'Watching paused. No scheduled scans are running.'
            : 'Not watched. Turn this on to be told when the architecture gets worse.';
        button.textContent = 'Watch this repository';
        return;
    }

    button.textContent = 'Stop watching';
    const parts = ['Watched. Rescanned daily.'];
    if (current.last_checked_at) {
        parts.push(`Last checked ${new Date(current.last_checked_at).toLocaleString()}.`);
    }
    if (current.last_status) parts.push(sanitize(current.last_status));
    status.textContent = parts.join(' ');
}

export async function toggleWatch() {
    const url = repoUrl();
    if (!url) return;

    const status = el('watch-status');
    const button = el('watch-toggle-btn');
    button.disabled = true;

    try {
        if (current && current.active) {
            const res = await fetch(`/api/v1/watch/${current.id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ active: false })
            });
            if (!res.ok) throw new Error(await errorText(res));
            current.active = false;
        } else {
            const threshold = parseFloat(el('watch-threshold').value) || 5.0;
            const webhook = (el('watch-webhook').value || '').trim();
            const res = await fetch('/api/v1/watch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    repo_url: url,
                    health_drop_threshold: threshold,
                    webhook_url: webhook || null
                })
            });
            if (!res.ok) throw new Error(await errorText(res));
            current = (await res.json()).watched;
            // Never echo it back into the field: the server does not return it,
            // and leaving it on screen is the only copy still visible.
            el('watch-webhook').value = '';
        }
        renderWatchState();
    } catch (e) {
        // The message matters here -- a rejected webhook URL is the common case
        // and the user needs to know which part was refused.
        status.textContent = `Could not update: ${e.message}`;
    } finally {
        button.disabled = false;
    }
}

async function errorText(res) {
    try {
        const body = await res.json();
        return body.detail || `HTTP ${res.status}`;
    } catch {
        return `HTTP ${res.status}`;
    }
}
