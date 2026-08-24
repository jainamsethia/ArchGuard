export function sanitize(str) {
    if (str === null || str === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}


export function getSeverityClass(severity) {
    const s = (severity || 'low').toLowerCase();
    if (s === 'critical') return 'badge-critical';
    if (s === 'high') return 'badge-high';
    if (s === 'medium') return 'badge-medium';
    return 'badge-low';
}


export function violationLocationCell(v) {
    const file = v.file;
    const line = Number(v.line || 0);
    if (file && line > 0) {
        return `${sanitize(file)}:${line}`;
    }
    if (file) {
        return sanitize(file);
    }
    if (v.module) {
        return `${sanitize(v.module)} <span class="violation-loc-note">(module-level)</span>`;
    }
    return 'Global';
}


export function renderMarkdown(src) {
    const esc = (s) => s
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

    const inline = (s) => esc(s)
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');

    const lines = String(src).replace(/\r\n/g, '\n').split('\n');
    const out = [];
    let i = 0;
    let para = [];

    const flushPara = () => {
        if (para.length) {
            out.push(`<p>${inline(para.join(' '))}</p>`);
            para = [];
        }
    };

    while (i < lines.length) {
        const line = lines[i];

        if (!line.trim()) { flushPara(); i++; continue; }

        // Fenced code block
        if (/^\s*```/.test(line)) {
            flushPara();
            const body = [];
            i++;
            while (i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i++]);
            i++;
            out.push(`<pre class="md-code"><code>${esc(body.join('\n'))}</code></pre>`);
            continue;
        }

        // Heading
        const h = line.match(/^(#{1,6})\s+(.*)$/);
        if (h) {
            flushPara();
            const level = Math.min(h[1].length + 2, 6); // keep page hierarchy sane
            out.push(`<h${level} class="md-h">${inline(h[2])}</h${level}>`);
            i++;
            continue;
        }

        // Table: header row, separator row, then body rows
        if (line.includes('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
            flushPara();
            const cells = (r) => r.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
            const head = cells(line);
            i += 2;
            const body = [];
            while (i < lines.length && lines[i].includes('|') && lines[i].trim()) body.push(cells(lines[i++]));
            const thead = head.map(c => `<th>${inline(c)}</th>`).join('');
            const tbody = body.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('');
            out.push(`<table class="md-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table>`);
            continue;
        }

        // Lists (unordered / ordered)
        const isUl = /^\s*[-*+]\s+/.test(line);
        const isOl = /^\s*\d+[.)]\s+/.test(line);
        if (isUl || isOl) {
            flushPara();
            const tag = isUl ? 'ul' : 'ol';
            const items = [];
            const re = isUl ? /^\s*[-*+]\s+/ : /^\s*\d+[.)]\s+/;
            while (i < lines.length && re.test(lines[i])) {
                items.push(`<li>${inline(lines[i].replace(re, ''))}</li>`);
                i++;
            }
            out.push(`<${tag} class="md-list">${items.join('')}</${tag}>`);
            continue;
        }

        para.push(line.trim());
        i++;
    }
    flushPara();
    return out.join('');
}


export function getErrorStateHtml(message, retryFn) {
    const retryId = 'retry-' + Math.random().toString(36).slice(2, 9);
    setTimeout(() => {
        const btn = document.getElementById(retryId);
        if (btn) btn.addEventListener('click', retryFn);
    }, 0);
    return `
        <div class="empty-state error-state">
            <div class="empty-icon">⚠️</div>
            <h3 class="empty-title">Something went wrong</h3>
            <p class="empty-body">${sanitize(message)}</p>
            <button id="${retryId}" class="btn-action">Retry</button>
        </div>
    `;
}


export function getEmptyStateHtml(icon, title, body) {
    return `
        <div class="empty-state" style="margin-top:0;">
            <div class="empty-icon">${icon}</div>
            <h3 class="empty-title">${title}</h3>
            <p class="empty-body">${body}</p>
        </div>
    `;
}


export function decodeSseEvent(rawEvent) {
    const parts = [];
    for (const line of rawEvent.split('\n')) {
        if (line.startsWith('data:')) {
            // Exactly one optional leading space is part of the framing.
            parts.push(line.slice(5).replace(/^ /, ''));
        }
    }
    return parts.join('\n');
}


/**
 * Mark a region as updating, or done.
 *
 * Paired with aria-live, this is what stops a screen reader announcing a
 * half-filled region: assistive technology holds announcements for a subtree
 * while aria-busy is true and reads the finished result once it clears. The
 * AI Advisor is the clearest case -- it streams an answer token by token, and
 * a live region without this announces every partial fragment.
 *
 * Always clear it in a `finally`. A region left permanently busy is worse than
 * one never marked, because announcements for it stay suppressed for good.
 */
export function setBusy(el, busy) {
    if (!el) return;
    el.setAttribute('aria-busy', busy ? 'true' : 'false');
}
