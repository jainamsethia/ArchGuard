import { safeFetch } from './api.js';
import { sanitize } from './dom.js';


/**
 * Stop offering what this instance cannot do.
 *
 * The AI features degrade rather than fail loudly: without `GEMINI_API_KEY` the
 * Advisor and remediation plans return an error per request. Honest, but badly
 * timed -- someone types a question, waits for a round trip, and is told the
 * feature was never going to work. Worse when the key is set and the configured
 * model id is wrong, because the error then reads like a credential problem.
 *
 * Asked once at load. A failed request leaves the controls enabled: not knowing
 * whether AI works is not the same as knowing it does not, and disabling a
 * working feature because a status call failed would be the worse mistake.
 */

/** Controls that do nothing useful without a working model. */
const AI_CONTROLS = [
    'btn-advisor-ask',
    'advisor-question-input',
    'remediation-btn',
    'violations-remediation-btn',
];

export async function applyCapabilities() {
    const caps = await safeFetch('/api/v1/capabilities', null);
    if (!caps || !caps.ai) return;

    if (caps.ai.available) {
        setAiEnabled(true, '');
        return;
    }
    setAiEnabled(false, caps.ai.reason || 'AI features are not configured.');
}


export function setAiEnabled(enabled, reason) {
    for (const id of AI_CONTROLS) {
        const el = document.getElementById(id);
        if (!el) continue;
        el.disabled = !enabled;
        if (enabled) {
            el.removeAttribute('title');
            el.removeAttribute('aria-describedby');
        } else {
            // Both, because the two audiences read different things: a
            // pointer user gets the tooltip, a screen-reader user gets the
            // description. `disabled` alone says "no" without saying why.
            el.title = reason;
            el.setAttribute('aria-describedby', 'ai-unavailable-note');
        }
    }

    const note = document.getElementById('ai-unavailable-note');
    if (!note) return;
    if (enabled) {
        note.hidden = true;
        note.textContent = '';
    } else {
        note.hidden = false;
        note.innerHTML = sanitize(reason);
    }
}
