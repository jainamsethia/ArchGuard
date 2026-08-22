import { jobQuery } from '../api.js';
import { updateEvolutionChart } from './charts.js';


export function updateEvolutionTrends(evoData) {
    function updateCard(type, trend) {
        const valEl = document.getElementById(`trend-${type}-val`);
        const statusEl = document.getElementById(`trend-${type}-status`);

        if (!trend || trend.current_value === null || trend.current_value === undefined) {
            valEl.textContent = '--';
            statusEl.textContent = 'Not enough scan history yet';
            statusEl.style.color = 'var(--text-secondary)';
            return;
        }

        // Format value depending on type
        if (type === 'violation') {
            valEl.textContent = Math.round(trend.current_value);
        } else if (type === 'fitness') {
            valEl.textContent = `${(trend.current_value * 100).toFixed(1)}%`;
        } else {
            valEl.textContent = trend.current_value.toFixed(2);
        }

        const cls = trend.classification;
        let icon = '';
        let color = 'var(--text-secondary)';

        if (cls === 'improving') {
            icon = '↑ Improving';
            color = 'var(--success-color)';
        } else if (cls === 'declining') {
            icon = '↓ Declining';
            color = 'var(--danger-color)';
        } else if (cls === 'insufficient') {
            icon = 'N/A — Insufficient data';
        } else {
            icon = '→ Stable';
        }

        statusEl.textContent = icon;
        statusEl.style.color = color;
    }

    updateCard('health', evoData.health_trend);
    updateCard('debt', evoData.debt_trend);
    updateCard('violation', evoData.violation_trend);
    updateCard('fitness', evoData.fitness_trend);
}


export async function startEvolutionAnalysis() {
    const btn = document.getElementById('start-evolution-btn');

    btn.disabled = true;
    btn.textContent = "Analyzing...";

    try {
        const res = await fetch(`/api/v1/evolution/analyze${jobQuery}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ max_commits: 10 })
        });
        const data = await res.json();

        if (data.error) {
            document.getElementById('trend_direction').textContent = data.message || data.error;
        } else {
            _applyGitEvolutionData(data);
        }
    } catch (err) {
        console.error("Evolution analysis failed:", err);
        document.getElementById('trend_direction').textContent = "Request failed.";
    } finally {
        btn.disabled = false;
        btn.textContent = "Analyze Git History";
    }
}


export function _applyGitEvolutionData(data) {
    const velEl = document.getElementById('debt_velocity');
    const trendEl = document.getElementById('trend_direction');
    const countEl = document.getElementById('evo-commits-count');

    // Nothing was measured. Rendering debt_velocity 0.0000 here showed a
    // failed run as a perfectly stable repository; say what actually
    // happened instead.
    if (!data.snapshots || data.snapshots.length === 0) {
        velEl.textContent = '—';
        velEl.style.color = 'var(--text-secondary)';
        countEl.textContent = '0';
        trendEl.textContent = data.message
            || 'No commits could be analysed — nothing measured.';
        trendEl.style.color = 'var(--warn-color)';
        return;
    }
    trendEl.style.color = '';

    if (data.debt_velocity !== undefined && data.debt_velocity !== null) {
        velEl.textContent = (data.debt_velocity > 0 ? '+' : '') + data.debt_velocity.toFixed(4);
        velEl.style.color = data.debt_velocity > 0 ? 'var(--danger-color)' : (data.debt_velocity < 0 ? 'var(--success-color)' : 'var(--text-primary)');
    }
    if (data.trend_direction) {
        trendEl.textContent = data.trend_direction.toUpperCase();
    }
    if (data.commits_analyzed) {
        countEl.textContent = data.commits_analyzed;
    }
    // A partial failure still produces real numbers, but for fewer
    // commits than were attempted -- note the gap rather than letting
    // the result read as complete.
    if (data.commits_failed) {
        trendEl.textContent += ` (${data.commits_failed} of `
            + `${data.commits_attempted} commits could not be analysed)`;
        trendEl.style.color = 'var(--warn-color)';
    }
    if (data.snapshots && data.snapshots.length > 0) {
        updateEvolutionChart(data.snapshots);
    }
}
