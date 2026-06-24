

// Generated event listeners
document.addEventListener("DOMContentLoaded", () => {
    document.getElementById('gen-id-click-9273a72d').addEventListener('click', function(event) { document.getElementById('fitness-container').scrollIntoView({behavior: 'smooth'}) });
    document.getElementById('gen-id-click-e936a072').addEventListener('click', function(event) { document.getElementById('advisor-panel').scrollIntoView({behavior: 'smooth'}) });
    document.getElementById('gen-id-click-136b5c30').addEventListener('click', function(event) { document.getElementById('evolution-trends-grid').scrollIntoView({behavior: 'smooth'}) });
    document.getElementById('gen-id-click-d33f8140').addEventListener('click', function(event) { switchTab('overview') });
    document.getElementById('gen-id-click-963abe3f').addEventListener('click', function(event) { switchTab('violations') });
    document.getElementById('start-evolution-btn').addEventListener('click', function(event) { startEvolutionAnalysis() });
    document.getElementById('scan-deps-btn').addEventListener('click', function(event) { scanDependencies() });
    document.getElementById('advisor-question-input').addEventListener('keydown', function(event) { if(event.key==='Enter') sendAdvisorQuestion() });
    document.getElementById('gen-id-click-1e6913c1').addEventListener('click', function(event) { sendAdvisorQuestion() });
    document.getElementById('remediation-btn').addEventListener('click', function(event) { generateRemediationPlan() });
});


// Extracted inline script

        let trendChartInstance = null;
        let moduleChartInstance = null;
        let evolutionChartInstance = null;

        // Configuration for dark mode charts
        Chart.defaults.color = '#94a3b8';
        Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.1)';
        Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

        const urlParams = new URLSearchParams(window.location.search);
        const highlightJobId = urlParams.get('job_id');
        
        // Use highlightJobId for initial fetch queries
        const jobQuery = highlightJobId ? `?job_id=${highlightJobId}` : '';
        const jobQueryAmp = highlightJobId ? `&job_id=${highlightJobId}` : '';

        // Clean the URL param as requested
        if (highlightJobId) {
            history.replaceState({}, '', 'dashboard.html');
        }

        async function safeFetch(url, fallback) {
            try {
                const resp = await fetch(url);
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                return await resp.json();
            } catch (e) {
                console.warn(`[dashboard] ${url} failed:`, e.message);
                return fallback;
            }
        }

        async function fetchData() {
            document.getElementById('refresh-loader').style.display = 'inline-block';
            try {
                const [runsData, latestData, modulesData, evolutionData, gitEvoData] = await Promise.all([
                    safeFetch(`/api/runs?limit=30${jobQueryAmp}`, { runs: [] }),
                    safeFetch(`/api/runs/latest${jobQuery}`, null),
                    safeFetch(`/api/modules${jobQuery}`, { modules: [] }),
                    safeFetch(`/api/evolution/trends${jobQuery}`, { trends: [] }),
                    safeFetch(`/api/evolution/latest${jobQuery}`, null)
                ]);

                if (!runsData?.runs?.length && !latestData) {
                    const mainContainer = document.querySelector('.metrics-grid').parentElement;
                    mainContainer.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 2rem;">
                            <h1 style="margin: 0; font-size: 2.5rem; background: linear-gradient(to right, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">ArchGuard Dashboard</h1>
                        </div>
                        <div class="empty-state">
                            <div class="empty-icon">🏗️</div>
                            <h3 class="empty-title">No analyses yet</h3>
                            <p class="empty-body">
                                Analyze a GitHub repository to see architectural health data here.
                            </p>
                            <a href="index.html" class="btn-primary">Analyze a Repository →</a>
                        </div>
                    `;
                    document.getElementById('refresh-loader').style.display = 'none';
                    return;
                }

                if (highlightJobId) {
                    // Try to fetch specific job if available
                    safeFetch(`/api/jobs/${highlightJobId}`, null).then(job => {
                        const overview = document.getElementById('overview');
                        if (overview && !document.getElementById('job-banner')) {
                            const banner = document.createElement('div');
                            banner.id = 'job-banner';
                            banner.className = 'glass-card';
                            banner.style.marginBottom = '2rem';
                            banner.style.background = 'rgba(16, 185, 129, 0.2)';
                            banner.style.borderColor = 'rgba(16, 185, 129, 0.4)';
                            banner.style.transition = 'all 2s ease';
                            
                            const jobStatus = job ? (job.status === 'COMPLETED' ? 'Successful' : job.status) : 'Completed';
                            banner.innerHTML = `
                                <h3 style="margin: 0; color: var(--success-color);">Analysis ${jobStatus}: ${highlightJobId}</h3>
                                <p style="margin: 0.5rem 0 0 0;">Your results have been successfully loaded.</p>
                            `;
                            
                            overview.insertBefore(banner, overview.firstChild);
                            setTimeout(() => {
                                banner.style.background = 'var(--surface-color)';
                                banner.style.borderColor = 'var(--border-color)';
                            }, 3000);
                        }
                    });
                }

                if (latestData) {
                    window.latestRun = latestData;
                    updateMetrics(latestData);
                    updateFitnessPanel(latestData);
                    updateViolationsTable(latestData);
                }
                updateTrendChart(runsData.runs);
                updateModuleChart(modulesData?.modules);
                updateEvolutionTrends(evolutionData);
                if (gitEvoData && gitEvoData.snapshots && gitEvoData.snapshots.length > 0) {
                    _applyGitEvolutionData(gitEvoData);
                }

                document.getElementById('last-updated').textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
            } catch (error) {
                console.error("Error fetching dashboard data:", error);
                document.getElementById('last-updated').textContent = 'Error updating';
            } finally {
                document.getElementById('refresh-loader').style.display = 'none';
            }
        }

        function updateMetrics(latestRun) {
            if (!latestRun || !latestRun.score) return;

            document.getElementById('current-score').textContent = latestRun.score.toFixed(1);

            const bandEl = document.getElementById('current-band');
            bandEl.textContent = latestRun.band || 'UNKNOWN';

            // Color coding based on band
            if (latestRun.band === 'PASS' || latestRun.band === 'HEALTHY') bandEl.style.color = 'var(--success-color)';
            else if (latestRun.band === 'WARN' || latestRun.band === 'WATCH') bandEl.style.color = 'var(--warn-color)';
            else if (latestRun.band === 'FAIL' || latestRun.band === 'CRITICAL') bandEl.style.color = 'var(--danger-color)';

            const violations = latestRun.violations || [];
            document.getElementById('active-violations').textContent = violations.length;
        }

        function getSeverityClass(severity) {
            const s = (severity || 'low').toLowerCase();
            if (s === 'critical') return 'badge-critical';
            if (s === 'high') return 'badge-high';
            if (s === 'medium') return 'badge-medium';
            return 'badge-low';
        }

        function sanitize(str) {
            if (str === null || str === undefined) return '';
            const div = document.createElement('div');
            div.textContent = String(str);
            return div.innerHTML;
        }

        function updateFitnessPanel(latestRun) {
            const container = document.getElementById('fitness-container');
            const metrics = latestRun.metrics || {};
            const fitnessResults = metrics.fitness_results || [];

            if (fitnessResults.length === 0) {
                container.innerHTML = '<div style="color: var(--text-secondary); padding: 1rem; text-align: center;">No fitness functions defined or executed.</div>';
                return;
            }

            const html = fitnessResults.map(r => {
                const passed = r.passed !== false;
                const severity = r.severity || 'warn';
                let statusClass = 'pass';
                let icon = '✅';
                
                if (!passed) {
                    if (severity === 'critical') {
                        statusClass = 'fail';
                        icon = '❌';
                    } else {
                        statusClass = 'warn';
                        icon = '⚠️';
                    }
                }
                
                const rule = sanitize(r.rule || '');
                const name = sanitize(r.name || r.rule || 'Unknown');
                const evidence = sanitize(r.evidence || '');
                
                let evidenceHtml = '';
                if (!passed && evidence) {
                    evidenceHtml = `<div class="fitness-card-evidence">${evidence}</div>`;
                }
                
                return `
                    <div class="fitness-card ${statusClass}">
                        <div class="fitness-card-header">
                            <span class="fitness-card-title">${icon} ${name}</span>
                            <span class="fitness-card-rule" title="${rule}">${rule}</span>
                        </div>
                        ${evidenceHtml}
                    </div>
                `;
            }).join('');

            container.innerHTML = `<div class="fitness-grid">${html}</div>`;
        }

        function updateViolationsTable(latestRun) {
            const tbody = document.querySelector('#violationsTable tbody');
            const violations = latestRun.violations || [];

            if (violations.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary);">No active violations. Great job! 🎉</td></tr>';
                return;
            }

            tbody.innerHTML = violations.slice(0, 20).map(v => `
                <tr>
                    <td>L${sanitize(v.layer || '?')}</td>
                    <td><span class="badge ${getSeverityClass(v.severity)}">${sanitize(v.severity || 'low')}</span></td>
                    <td style="color: #cbd5e1;">${sanitize(v.file || v.module || 'Global')}</td>
                    <td>${sanitize(v.message || 'Unknown violation')}</td>
                </tr>
            `).join('');
        }

        function updateTrendChart(runs) {
            if (!runs || runs.length === 0) return;

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
                        label: 'ArchDebt Score',
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

        function updateModuleChart(modulesData) {
            if (!modulesData) return;

            const labels = Object.keys(modulesData);
            const data = Object.values(modulesData);

            // Sort by score descending
            const combined = labels.map((l, i) => ({label: l, data: data[i]}));
            combined.sort((a, b) => b.data - a.data);

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

        function updateEvolutionTrends(evoData) {
            function updateCard(type, trend) {
                const valEl = document.getElementById(`trend-${type}-val`);
                const statusEl = document.getElementById(`trend-${type}-status`);
                
                if (!trend || trend.current_value === null || trend.current_value === undefined) {
                    valEl.textContent = 'N/A';
                    statusEl.textContent = 'Insufficient data';
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

                const cls = trend.classification; // 'improving', 'stable', 'declining'
                let icon = '';
                let color = 'var(--text-secondary)';
                
                if (cls === 'improving') {
                    icon = '↑ Improving';
                    color = 'var(--success-color)';
                } else if (cls === 'declining') {
                    icon = '↓ Declining';
                    color = 'var(--danger-color)';
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

        async function scanDependencies() {
            const btn = document.getElementById('scan-deps-btn');
            const statusEl = document.getElementById('deps-status');
            const scoreEl = document.getElementById('deps-score');
            const tableContainer = document.getElementById('deps-table-container');
            const tbody = document.querySelector('#depsTable tbody');
            
            btn.disabled = true;
            btn.textContent = "Scanning...";
            statusEl.textContent = "Running pip-audit... (this may take a while)";
            
            try {
                const res = await fetch(`/api/v1/deps${jobQuery}`);
                const data = await res.json();
                
                if (data.skipped) {
                    statusEl.textContent = `Skipped: ${data.skip_reason}`;
                    scoreEl.textContent = '--';
                    tableContainer.style.display = 'none';
                } else {
                    statusEl.textContent = `Scanned ${data.scanned_packages} packages. Found ${data.vulnerable_packages.length} vulnerabilities.`;
                    scoreEl.textContent = data.score.toFixed(1);
                    
                    if (data.vulnerable_packages.length > 0) {
                        tbody.innerHTML = data.vulnerable_packages.map(v => `
                            <tr>
                                <td style="font-weight: 600;">${sanitize(v.package)}</td>
                                <td>${sanitize(v.version)}</td>
                                <td><span class="badge badge-critical">${sanitize(v.id)}</span></td>
                                <td style="font-size: 0.875rem; color: var(--text-secondary);">${sanitize(v.description)}</td>
                            </tr>
                        `).join('');
                        tableContainer.style.display = 'block';
                    } else {
                        tableContainer.style.display = 'none';
                        statusEl.textContent += " All dependencies are healthy! 🎉";
                    }
                }
            } catch (err) {
                console.error("Dependency scan failed:", err);
                statusEl.textContent = "Error scanning dependencies.";
            } finally {
                btn.disabled = false;
                btn.textContent = "Scan Dependencies";
            }
        }

        function switchTab(name) {
            document.querySelectorAll('.tab').forEach(t => t.setAttribute('aria-selected', 'false'));
            document.querySelectorAll('.tab-panel-main').forEach(p => p.classList.remove('active'));
            
            const targetTab = document.querySelector(`.tab[aria-controls="${name}"]`);
            if (targetTab) {
                targetTab.setAttribute('aria-selected', 'true');
            }
            const targetPanel = document.getElementById(name);
            if (targetPanel) {
                targetPanel.classList.add('active');
            }
            
            history.pushState({}, '', window.location.search + `#${name}`);
            
            if (name === 'dependencies' && window._visNet) {
                window._visNet.redraw();
            }
        }

        window.addEventListener('load', () => {
            const hash = window.location.hash.substring(1);
            if (['overview', 'violations'].includes(hash)) {
                switchTab(hash);
            }
        });
        
        window.addEventListener('hashchange', () => {
            const hash = window.location.hash.substring(1);
            if (['overview', 'violations'].includes(hash)) {
                switchTab(hash);
            } else if (!hash) {
                switchTab('overview');
            }
        });

        // Initial 
        async function startEvolutionAnalysis() {
            const btn = document.getElementById('start-evolution-btn');
            
            btn.disabled = true;
            btn.textContent = "Analyzing...";
            
            try {
                const res = await fetch(`/api/evolution/analyze${jobQuery}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ max_commits: 10 })
                });
                const data = await res.json();
                
                if (data.error) {
                    document.getElementById('trend_direction').textContent = data.error;
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
        
        function _applyGitEvolutionData(data) {
            const velEl = document.getElementById('debt_velocity');
            const trendEl = document.getElementById('trend_direction');
            const countEl = document.getElementById('evo-commits-count');
            
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
            if (data.snapshots && data.snapshots.length > 0) {
                updateEvolutionChart(data.snapshots);
            }
        }
        
        function updateEvolutionChart(snapshots) {
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

        // Fetch initial data
        fetchData();
        setInterval(fetchData, 30000); // 30 seconds

        // ─── Advisor Panel (Step 11/12 – Anthropic Streaming) ───
        async function sendAdvisorQuestion() {
            const input = document.getElementById('advisor-question-input');
            const responseEl = document.getElementById('advisor-response');
            const question = input.value.trim();
            if (!question) return;

            responseEl.textContent = '▌';  // blinking cursor placeholder
            input.value = '';
            input.disabled = true;

            let contextStr = "No context data available.";
            if (window.latestRun) {
                const run = window.latestRun;
                const violationsCount = run.violations ? run.violations.length : 0;
                contextStr = `Current Health Score: ${run.score || 0}\nGrade: ${run.band || 'UNKNOWN'}\nActive Violations: ${violationsCount}`;
            }

            try {
                const res = await fetch(`/api/v1/advisor/ask${jobQuery}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ question, context: contextStr })
                });

                if (!res.ok) {
                    responseEl.textContent = `Error ${res.status}: ${res.statusText}`;
                    return;
                }

                // Consume the SSE stream chunk-by-chunk
                const reader = res.body.getReader();
                const decoder = new TextDecoder();
                let accumulated = '';

                responseEl.textContent = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;

                    const raw = decoder.decode(value, { stream: true });
                    // Each SSE event is "data: <text>\n\n"
                    const lines = raw.split('\n');
                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const chunk = line.slice(6);  // strip "data: "
                            accumulated += chunk;
                            responseEl.textContent = accumulated + '▌';
                        }
                    }
                }

                // Remove trailing cursor
                responseEl.textContent = accumulated || 'No response received.';

            } catch (err) {
                console.error('Advisor streaming error:', err);
                responseEl.textContent = 'Error communicating with AI Advisor.';
            } finally {
                input.disabled = false;
                input.focus();
            }
        }

        // ─── Remediation Panel (Step 17) ───

        async function generateRemediationPlan() {
            const btn = document.getElementById('remediation-btn');
            const resultsEl = document.getElementById('remediation-results');

            btn.disabled = true;
            btn.textContent = 'Generating...';
            resultsEl.innerHTML = '<div style="color: var(--text-secondary);">Generating remediation plan...</div>';

            try {
                const res = await fetch(`/api/remediation/plan${jobQuery}`);
                const data = await res.json();
                const tasks = data.tasks || [];

                if (tasks.length === 0) {
                    resultsEl.innerHTML = '<div style="color: var(--success-color);">No remediation tasks needed. Architecture is healthy! 🎉</div>';
                } else {
                    resultsEl.innerHTML = tasks.map((t, i) => {
                        const badgeClass = 'badge-' + (t.priority || 'medium');
                        const criteria = (t.acceptance_criteria || []).map(c => `<li>${sanitize(c)}</li>`).join('');
                        return `
                            <div style="background: rgba(30,41,59,0.4); border-radius: 8px; padding: 1rem; margin-bottom: 0.75rem; border-left: 4px solid ${t.priority === 'critical' ? 'var(--danger-color)' : t.priority === 'high' ? 'var(--warn-color)' : 'var(--accent-color)'};">
                                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.5rem;">
                                    <span style="font-weight: 600; color: var(--text-primary);">${sanitize(t.title)}</span>
                                    <div style="display: flex; gap: 0.5rem; align-items: center;">
                                        <span class="badge ${badgeClass}">${sanitize(t.priority)}</span>
                                        <span style="font-size: 0.75rem; color: var(--text-secondary);">${t.effort_days || '?'}d</span>
                                    </div>
                                </div>
                                <div style="font-size: 0.875rem; color: var(--text-secondary); margin-bottom: 0.5rem;">${sanitize(t.description)}</div>
                                ${criteria ? `<ul style="margin: 0.25rem 0 0 1rem; padding: 0; font-size: 0.8rem; color: var(--text-secondary);">${criteria}</ul>` : ''}
                            </div>
                        `;
                    }).join('');
                }
            } catch (err) {
                console.error('Remediation error:', err);
                resultsEl.innerHTML = '<div style="color: var(--danger-color);">Error generating remediation plan.</div>';
            } finally {
                btn.disabled = false;
                btn.textContent = 'Generate Plan';
            }
        }
    