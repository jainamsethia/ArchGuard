import { getEmptyStateHtml } from '../dom.js';


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
