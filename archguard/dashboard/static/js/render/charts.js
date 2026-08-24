import { getEmptyStateHtml } from '../dom.js';


/**
 * Give a canvas chart a text alternative.
 *
 * A <canvas> is a bitmap: to a screen reader an unlabelled one is not "a
 * chart" or "an image", it is nothing at all, and the numbers plotted in it
 * exist only as pixels. So the alternative here is the data rather than a
 * description of the picture -- the canvas is announced as an image with a
 * one-line summary, and an sr-only table carries the same values the chart
 * draws.
 *
 * Built with createElement/textContent rather than innerHTML: module names and
 * timestamps come from the API, and this way there is no escaping to get wrong.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {string} label    one-line summary, used as the canvas's accessible name
 * @param {string} caption  the table's caption
 * @param {[string, string]} columns  header for the row label and the value
 * @param {Array<[string, number|string]>} rows
 *
 * WCAG 1.1.1 Non-text Content.
 */
function describeChart(canvas, { label, caption, columns, rows }) {
    if (!canvas) return;

    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', label);

    const id = `${canvas.id}-table`;
    let table = document.getElementById(id);
    if (!table) {
        table = document.createElement('table');
        table.id = id;
        table.className = 'sr-only';
        // After the canvas, so it is read in the order the chart appears
        // rather than announced before the heading it belongs to.
        canvas.insertAdjacentElement('afterend', table);
    }
    // Replaced wholesale on every render. The update path below returns early
    // once a Chart instance exists, so a table built only on the first render
    // would keep showing that render's numbers forever.
    table.textContent = '';

    const cap = document.createElement('caption');
    cap.textContent = caption;
    table.appendChild(cap);

    const thead = document.createElement('thead');
    const headRow = document.createElement('tr');
    for (const name of columns) {
        const th = document.createElement('th');
        th.scope = 'col';
        th.textContent = name;
        headRow.appendChild(th);
    }
    thead.appendChild(headRow);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    for (const [name, value] of rows) {
        const tr = document.createElement('tr');
        const th = document.createElement('th');
        th.scope = 'row';
        th.textContent = name;
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(th, td);
        tbody.appendChild(tr);
    }
    table.appendChild(tbody);
}


/**
 * One handle per chart, so an update replaces the drawing rather than stacking
 * a second canvas on top of the first.
 */
let trendChartInstance = null;
let moduleChartInstance = null;
let evolutionChartInstance = null;

/**
 * Chart.js reads these once at construction, so they are applied on import
 * rather than per chart.
 */
export function configureChartDefaults() {
    Chart.defaults.color = '#94a3b8';
    Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.1)';
    Chart.defaults.font.family = "'Inter', -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif";
}


export function updateTrendChart(runs) {
    if (!runs || runs.length < 2) {
        // Expected, not an error: every analysis job clones a fresh
        // workspace and records exactly one run, so a repository needs
        // to be scanned more than once before there is a trend to draw.
        const ctx = document.getElementById('trendChart');
        const n = (runs || []).length;
        if (ctx && ctx.parentElement) ctx.parentElement.innerHTML = getEmptyStateHtml(
            '📈',
            'Not enough scan history yet',
            `This repository has ${n} recorded scan${n === 1 ? '' : 's'}. `
            + 'Analyse it again to start building a trend over time.'
        );
        return;
    }

    // Sort runs chronologically
    const sortedRuns = [...runs].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

    const labels = sortedRuns.map(r => {
        const d = new Date(r.timestamp);
        return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}:${d.getMinutes().toString().padStart(2, '0')}`;
    });
    const data = sortedRuns.map(r => r.score || 0);

    // Before the early return below, so the update path refreshes it too.
    describeChart(document.getElementById('trendChart'), {
        label: `Line chart: health score across ${data.length} scans, `
             + `from ${data[0]} at the oldest to ${data[data.length - 1]} at the most recent.`,
        caption: 'Health score by scan',
        columns: ['Scan', 'Health score'],
        rows: labels.map((l, i) => [l, data[i]]),
    });

    if (trendChartInstance) {
        trendChartInstance.data.labels = labels;
        trendChartInstance.data.datasets[0].data = data;
        trendChartInstance.update();
        return;
    }

    const ctx = document.getElementById('trendChart').getContext('2d');

    // Create gradient
    const gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, 'rgba(59, 130, 246, 0.5)');
    gradient.addColorStop(1, 'rgba(59, 130, 246, 0.0)');

    trendChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Health Score',
                data: data,
                borderColor: '#3b82f6',
                backgroundColor: gradient,
                borderWidth: 2,
                fill: true,
                tension: 0.4,
                pointBackgroundColor: '#0f172a',
                pointBorderColor: '#3b82f6',
                pointBorderWidth: 2,
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(15, 23, 42, 0.9)',
                    titleColor: '#f8fafc',
                    bodyColor: '#cbd5e1',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' }
                },
                x: {
                    grid: { display: false },
                    ticks: { maxTicksLimit: 8 }
                }
            }
        }
    });
}


export function updateModuleChart(modulesData) {
    if (!modulesData || Object.keys(modulesData).length === 0) {
        const ctx = document.getElementById('moduleChart');
        if(ctx && ctx.parentElement) ctx.parentElement.innerHTML = getEmptyStateHtml('🧩', 'No Modules', 'Module complexity data is not available.');
        return;
    }

    const labels = Object.keys(modulesData);
    const data = Object.values(modulesData);

    // Sort by score descending
    const combined = labels.map((l, i) => ({label: l, data: data[i]}));
    combined.sort((a, b) => b.data - a.data);

    const noteEl = document.getElementById('module-chart-note');
    if (noteEl) {
        if (combined.length > 10) {
            noteEl.textContent = `Showing top 10 of ${combined.length} modules by health score.`;
        } else {
            noteEl.textContent = '';
        }
    }

    const sortedLabels = combined.slice(0, 10).map(x => x.label);
    const sortedData = combined.slice(0, 10).map(x => x.data);

    describeChart(document.getElementById('moduleChart'), {
        label: combined.length > 10
            ? `Bar chart: health score for the top 10 of ${combined.length} modules, highest first.`
            : `Bar chart: health score for ${combined.length} modules, highest first.`,
        caption: 'Module health score, highest first',
        columns: ['Module', 'Health score'],
        rows: sortedLabels.map((l, i) => [l, sortedData[i]]),
    });

    if (moduleChartInstance) {
        moduleChartInstance.data.labels = sortedLabels;
        moduleChartInstance.data.datasets[0].data = sortedData;
        moduleChartInstance.update();
        return;
    }

    const ctx = document.getElementById('moduleChart').getContext('2d');
    moduleChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: sortedLabels,
            datasets: [{
                label: 'Module Score',
                data: sortedData,
                backgroundColor: 'rgba(139, 92, 246, 0.6)',
                borderColor: 'rgba(139, 92, 246, 1)',
                borderWidth: 1,
                borderRadius: 4
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    beginAtZero: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' }
                },
                y: {
                    grid: { display: false }
                }
            }
        }
    });
}


export function updateEvolutionChart(snapshots) {
    if (!snapshots || snapshots.length === 0) {
        const ctx = document.getElementById('evolutionChart');
        if(ctx && ctx.parentElement) ctx.parentElement.innerHTML = getEmptyStateHtml('🕓', 'No Evolution Data', 'Run a Git history analysis to see the evolution chart.');
        return;
    }
    const labels = snapshots.map(s => {
        const d = new Date(s.committed_at);
        return `${d.getMonth()+1}/${d.getDate()} ${s.sha.substring(0, 7)}`;
    });
    const data = snapshots.map(s => s.health_score);

    describeChart(document.getElementById('evolutionChart'), {
        label: `Line chart: health score across ${data.length} commits, `
             + `from ${data[0]} at the oldest to ${data[data.length - 1]} at the most recent.`,
        caption: 'Health score by commit',
        columns: ['Commit', 'Health score'],
        rows: labels.map((l, i) => [l, data[i]]),
    });

    if (evolutionChartInstance) {
        evolutionChartInstance.data.labels = labels;
        evolutionChartInstance.data.datasets[0].data = data;
        evolutionChartInstance.update();
        return;
    }

    const ctx = document.getElementById('evolutionChart').getContext('2d');
    const gradient = ctx.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, 'rgba(16, 185, 129, 0.5)');
    gradient.addColorStop(1, 'rgba(16, 185, 129, 0.0)');

    evolutionChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Commit Health Score',
                data: data,
                borderColor: '#10b981',
                backgroundColor: gradient,
                borderWidth: 2,
                fill: true,
                tension: 0.1,
                pointBackgroundColor: '#0f172a',
                pointBorderColor: '#10b981',
                pointBorderWidth: 2,
                pointRadius: 4,
                pointHoverRadius: 6
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                y: { beginAtZero: false, min: 0, max: 100, grid: { color: 'rgba(255, 255, 255, 0.05)' } },
                x: { grid: { display: false }, ticks: { maxTicksLimit: 10 } }
            }
        }
    });
}
