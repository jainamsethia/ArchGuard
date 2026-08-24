import { jobQuery } from '../api.js';
import { decodeSseEvent, getErrorStateHtml, renderMarkdown, setBusy } from '../dom.js';


export async function sendAdvisorQuestion() {
    const askBtn = document.getElementById('btn-advisor-ask');
    if (askBtn.disabled) return;  // already in flight -- ignore a duplicate click or Enter-key repeat

    const input = document.getElementById('advisor-question-input');
    const responseEl = document.getElementById('advisor-response');
    const question = input.value.trim();
    if (!question) return;

    responseEl.textContent = '▌';  // blinking cursor placeholder
    // The blinking cursor is the sighted user's "working on it". aria-busy is
    // the same signal for everyone else, and on a live region it is also what
    // holds the announcement back until the answer is complete rather than
    // reading out every partial token as it streams in below.
    setBusy(responseEl, true);
    input.value = '';
    input.disabled = true;
    askBtn.disabled = true;

    try {
        const res = await fetch(`/api/v1/advisor/ask${jobQuery}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question })
        });

        if (!res.ok) {
            responseEl.innerHTML = getErrorStateHtml(`Error ${res.status}: ${res.statusText}`, () => sendAdvisorQuestion());
            return;
        }

        // Consume the SSE stream event-by-event.
        //
        // Events are separated by a blank line, and a single event may
        // carry several "data:" lines which must be rejoined with "\n".
        // Parsing line-by-line and concatenating without a separator
        // (the previous approach) flattened every newline and threw
        // away any line that was not itself a "data:" field -- which is
        // exactly what deleted markdown table rows mid-answer.
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let accumulated = '';
        let buffer = '';

        responseEl.textContent = '';

        const drainEvents = (flush) => {
            let sep;
            while ((sep = buffer.indexOf('\n\n')) !== -1) {
                const rawEvent = buffer.slice(0, sep);
                buffer = buffer.slice(sep + 2);
                accumulated += decodeSseEvent(rawEvent);
                responseEl.textContent = accumulated + '▌';
            }
            if (flush && buffer.trim()) {
                accumulated += decodeSseEvent(buffer);
                buffer = '';
            }
        };

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            drainEvents(false);
        }
        drainEvents(true);

        if (accumulated) {
            responseEl.innerHTML = renderMarkdown(accumulated);
        } else {
            responseEl.textContent = 'No response received.';
        }

    } catch (err) {
        console.error('Advisor streaming error:', err);
        responseEl.innerHTML = getErrorStateHtml('Error communicating with AI Advisor.', () => sendAdvisorQuestion());
    } finally {
        setBusy(responseEl, false);
        input.disabled = false;
        askBtn.disabled = false;
        input.focus();
    }
}
