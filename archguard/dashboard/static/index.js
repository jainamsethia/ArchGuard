        const urlInput = document.getElementById('github-url');
        const submitBtn = document.getElementById('btn-submit');
        const errorMsg = document.getElementById('error-msg');
        const repoPreview = document.getElementById('repo-preview');
        const terminal = document.getElementById('terminal');
        const termOutput = document.getElementById('term-output');
        const container = document.getElementById('main-container');
        const resultCard = document.getElementById('result-card');

        let isValidating = false;
        let isJobRunning = false;

        function resetSubmitButton() {
            submitBtn.textContent = 'Analyze Repository';
            submitBtn.disabled = false;
            urlInput.disabled = false;
            isJobRunning = false;
        }

        // Auto-validate URL when user finishes typing
        let debounceTimer;
        urlInput.addEventListener('input', () => {
            clearTimeout(debounceTimer);
            repoPreview.classList.remove('show');
            errorMsg.classList.remove('show');
            
            const url = urlInput.value.trim();
            if (url && url.includes('github.com')) {
                debounceTimer = setTimeout(() => validateUrl(url), 800);
            }
        });

        async function validateUrl(url) {
            if (isJobRunning) return;
            
            isValidating = true;
            submitBtn.disabled = true;
            submitBtn.textContent = 'Validating...';
            errorMsg.classList.remove('show');

            try {
                const res = await fetch('/api/jobs/validate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ github_url: url })
                });

                if (res.status === 429) {
                    errorMsg.textContent = 'GitHub API rate limit exceeded. Please wait 60 seconds.';
                    errorMsg.classList.add('show');
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Analyze Repository';
                    return;
                }

                if (!res.ok) {
                    const data = await res.json();
                    throw new Error(data.detail || 'Validation failed');
                }

                const data = await res.json();
                document.getElementById('preview-name').textContent = data.full_name;
                document.getElementById('preview-lang').textContent = data.language || 'Unknown';
                document.getElementById('preview-stars').textContent = data.stars;
                
                repoPreview.classList.add('show');
                submitBtn.disabled = false;
                submitBtn.textContent = 'Start Analysis';
            } catch (err) {
                errorMsg.textContent = err.message;
                errorMsg.classList.add('show');
                submitBtn.disabled = true;
                submitBtn.textContent = 'Analyze Repository';
            } finally {
                isValidating = false;
            }
        }

        function appendLog(msg, type = '') {
            const line = document.createElement('div');
            line.className = `terminal-line ${type}`;
            const timestamp = new Date().toISOString().split('T')[1].substring(0, 8);
            line.textContent = `[${timestamp}] ${msg}`;
            termOutput.appendChild(line);
            termOutput.scrollTop = termOutput.scrollHeight;
        }

        async function handleSubmit() {
            const url = urlInput.value.trim();
            if (!url || isJobRunning || isValidating) return;

            isJobRunning = true;
            urlInput.disabled = true;
            submitBtn.disabled = true;
            submitBtn.innerHTML = 'Analyzing<span class="loading-dots"></span>';
            
            // Show terminal
            container.classList.add('expanded');
            terminal.classList.add('show');
            termOutput.innerHTML = '';
            appendLog('Initializing ArchGuard pipeline...', 'system');
            appendLog(`Target repository: ${url}`, 'system');

            try {
                // Submit job
                const res = await fetch('/api/jobs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ github_url: url })
                });

                if (!res.ok) {
                    const err = await res.json();
                    throw new Error(err.detail || 'Failed to submit job');
                }

                const data = await res.json();
                const jobId = data.job_id;
                appendLog(`Job queued. ID: ${jobId}`, 'system');
                appendLog('Establishing secure stream connection...', 'system');

                // Connect to SSE stream
                const evtSource = new EventSource(`/api/jobs/${jobId}/stream`);
                
                evtSource.onmessage = function(event) {
                    const payload = JSON.parse(event.data);
                    
                    if (payload.type === 'progress') {
                        appendLog(payload.message);
                    } 
                    else if (payload.type === 'error') {
                        appendLog(`ERROR: ${payload.error}`, 'error');
                        submitBtn.textContent = 'Analysis Failed';
                        evtSource.close();
                    }
                    else if (payload.type === 'result' && payload.result) {
                        const r = payload.result;
                        document.getElementById('res-score').textContent = r.archdebt?.health_score != null ? parseFloat(r.archdebt.health_score).toFixed(1) : '--';
                        document.getElementById('res-violations').textContent = r.total_violations || '0';
                        const grade = r.archdebt?.health_grade || '--';
                        const gradeEl = document.getElementById('res-grade');
                        gradeEl.textContent = `Grade: ${grade}`;
                        
                        // Set colors based on grade (A/B: success, C/D: warning, F: danger)
                        if (['A', 'B'].includes(grade)) {
                            gradeEl.style.color = 'var(--success-color)';
                            gradeEl.style.background = 'rgba(16, 185, 129, 0.2)';
                            gradeEl.style.borderColor = 'rgba(16, 185, 129, 0.3)';
                        } else if (['C', 'D'].includes(grade)) {
                            gradeEl.style.color = '#f59e0b';
                            gradeEl.style.background = 'rgba(245, 158, 11, 0.2)';
                            gradeEl.style.borderColor = 'rgba(245, 158, 11, 0.3)';
                        } else {
                            gradeEl.style.color = 'var(--danger-color)';
                            gradeEl.style.background = 'rgba(239, 68, 68, 0.2)';
                            gradeEl.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                        }
                        
                        resultCard.classList.add('show');
                    }
                    else if (payload.type === 'done') {
                        appendLog('Pipeline finished. Generating dashboard...', 'system');
                        evtSource.close();
                        setTimeout(() => {
                            window.location.href = `dashboard.html?job_id=${encodeURIComponent(jobId)}`;
                        }, 1500);
                    }
                };

                let reconnectAttempts = 0;
                const MAX_RECONNECT = 5;
                evtSource.onerror = async function(err) {
                    reconnectAttempts++;
                    if (reconnectAttempts >= MAX_RECONNECT) {
                        evtSource.close();
                        appendLog('Connection failed. Checking job status...', 'error');
                        try {
                            const res = await fetch(`/api/jobs/${jobId}`);
                            if (!res.ok) throw new Error();
                            const job = await res.json();
                            if (job.status === 'FAILED') {
                                appendLog(`Analysis failed: ${job.error || 'Unknown error.'}`, 'error');
                                resetSubmitButton();
                            }
                        } catch (e) {
                            appendLog('Could not retrieve job status. Please try again.', 'error');
                        }
                        resetSubmitButton();
                        return;
                    }
                    appendLog(`Stream lost. Retrying (${reconnectAttempts}/${MAX_RECONNECT})...`, 'warn');
                };

            } catch (err) {
                appendLog(`Failed to start analysis: ${err.message}`, 'error');
                submitBtn.textContent = 'Submission Error';
                urlInput.disabled = false;
                isJobRunning = false;
            }
        }
