import { setBusy } from '../dom.js';


/**
 * The "watch this repository" toggle.
 *
 * Watching asks the worker to re-scan the repository on a schedule and report
 * regressions, so the control has to say which state it is in rather than what
 * it will do next -- hence aria-pressed on a toggle button instead of a label
 * that flips between "Watch" and "Unwatch" with nothing announcing the change.
 *
 * The button's state is refreshed from the data path, not from DOMContentLoaded.
 * The repository is only known once the latest run has been fetched, and the
 * compare control shipped the other way round once: its enable check ran before
 * the async fetch had filled anything in, so it stayed permanently disabled.
 */

let currentRepoUrl = null;

function button() {
    return document.getElementById('watch-toggle-btn');
}

function paint(btn, watching) {
    btn.setAttribute('aria-pressed', watching ? 'true' : 'false');
    btn.textContent = watching ? 'Watching' : 'Watch this repository';
    btn.title = watching
        ? 'This repository is re-scanned on a schedule. Click to stop.'
        : 'Re-scan this repository on a schedule and report regressions.';
}


/**
 * Show the toggle for *run*'s repository and reflect whether it is watched.
 *
 * Called from the poll cycle once a run is available, so a page opened on a
 * repository already being watched does not show an unpressed toggle until the
 * user clicks it.
 */
export async function syncWatchButton(run) {
    const btn = button();
    if (!btn) return;

    const url = run && run.repo_url;
    if (!url) {
        // No run, no repository to watch. Hidden rather than disabled: a
        // disabled control invites the reader to work out why.
        btn.hidden = true;
        currentRepoUrl = null;
        return;
    }

    currentRepoUrl = url;
    btn.hidden = false;

    try {
        const res = await fetch('/api/v1/watched');
        if (!res.ok) return;
        const data = await res.json();
        const watched = (data.watched || []).some(w => w.repo_url === url);
        paint(btn, watched);
    } catch (err) {
        // Leave the button as it is. Guessing a state would show the wrong one.
        console.error('Could not read the watch list:', err);
    }
}


export function initWatchToggle() {
    const btn = button();
    if (!btn) return;

    btn.addEventListener('click', async () => {
        if (!currentRepoUrl || btn.disabled) return;

        const watching = btn.getAttribute('aria-pressed') === 'true';
        btn.disabled = true;
        setBusy(btn, true);
        try {
            const res = await fetch('/api/v1/watched', {
                method: watching ? 'DELETE' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ repo_url: currentRepoUrl }),
            });
            if (res.ok) {
                paint(btn, !watching);
            } else {
                console.error('Watch toggle failed:', res.status);
            }
        } catch (err) {
            console.error('Watch toggle failed:', err);
        } finally {
            btn.disabled = false;
            setBusy(btn, false);
        }
    });
}
