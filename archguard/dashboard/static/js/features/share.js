import { setBusy } from '../dom.js';


/**
 * The "share this report" control.
 *
 * Sharing makes one analysis readable by anyone holding the link, so the
 * control has to be honest about which state it is in: a report that is
 * already shared must say so on load rather than looking unshared until the
 * user clicks. The run payload carries a `shared` boolean for exactly that --
 * never the token, which only the owner who asked for it should ever see.
 *
 * Like the watch toggle, the state comes from the poll cycle rather than
 * DOMContentLoaded, because the run is not known until the fetch lands.
 */

let currentJobId = null;

const el = (id) => document.getElementById(id);


function showLink(url) {
    const box = el('share-link-row');
    const input = el('share-link-input');
    if (!box || !input) return;
    input.value = url;
    box.hidden = false;
}


function hideLink() {
    const box = el('share-link-row');
    if (box) box.hidden = true;
    const input = el('share-link-input');
    if (input) input.value = '';
}


function paint(shared) {
    const btn = el('share-btn');
    if (!btn) return;
    btn.setAttribute('aria-pressed', shared ? 'true' : 'false');
    btn.textContent = shared ? 'Shared — manage link' : 'Share this report';
    const revoke = el('share-revoke-btn');
    if (revoke) revoke.hidden = !shared;
    if (!shared) hideLink();
}


/**
 * Reflect *run*'s share state. Called from the poll cycle.
 *
 * Deliberately does not reveal the link on load. Knowing a report is shared is
 * not the same as putting its URL on screen for whoever is looking at the
 * monitor -- the owner asks for it by clicking.
 */
export function syncShareButton(run) {
    const btn = el('share-btn');
    if (!btn) return;

    if (!run || !run.job_id) {
        btn.hidden = true;
        currentJobId = null;
        return;
    }
    currentJobId = run.job_id;
    btn.hidden = false;
    paint(Boolean(run.shared));
}


async function call(method) {
    return fetch(`/api/v1/runs/${encodeURIComponent(currentJobId)}/share`, {
        method,
        headers: { 'Content-Type': 'application/json' },
    });
}


export function initShareControls() {
    const btn = el('share-btn');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        if (!currentJobId || btn.disabled) return;
        btn.disabled = true;
        setBusy(btn, true);
        try {
            // POST is idempotent server-side: an already-shared run returns its
            // existing link rather than minting a second one, so this is safe
            // to use both to create a link and to reveal the current one.
            const res = await call('POST');
            if (res.ok) {
                const data = await res.json();
                paint(true);
                showLink(data.share_url);
            } else {
                console.error('Share failed:', res.status);
            }
        } catch (err) {
            console.error('Share failed:', err);
        } finally {
            btn.disabled = false;
            setBusy(btn, false);
        }
    });

    const revoke = el('share-revoke-btn');
    if (revoke) revoke.addEventListener('click', async () => {
        if (!currentJobId || revoke.disabled) return;
        revoke.disabled = true;
        setBusy(revoke, true);
        try {
            const res = await call('DELETE');
            if (res.ok) {
                // Only on success. Showing "not shared" for a link that is
                // still live is the dangerous direction of this error: the
                // owner would believe they had withdrawn access they still
                // have granted.
                paint(false);
            } else {
                console.error('Revoke failed:', res.status);
            }
        } catch (err) {
            console.error('Revoke failed:', err);
        } finally {
            revoke.disabled = false;
            setBusy(revoke, false);
        }
    });

    const copy = el('share-copy-btn');
    if (copy) copy.addEventListener('click', async () => {
        const input = el('share-link-input');
        if (!input || !input.value) return;
        try {
            await navigator.clipboard.writeText(input.value);
            copy.textContent = 'Copied';
            setTimeout(() => { copy.textContent = 'Copy'; }, 2000);
        } catch {
            // Clipboard access can be refused, and the input is right there and
            // selectable -- so select it rather than reporting a failure the
            // user can trivially work around.
            input.select();
        }
    });
}
