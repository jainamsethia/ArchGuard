from archguard.dashboard.job_manager import MAX_STORED_JOBS, JobManager, JobStatus


def test_eviction_skips_running_jobs():
    manager = JobManager()

    # Fill job manager to MAX_STORED_JOBS
    for i in range(MAX_STORED_JOBS):
        job = manager.create_job(f"http://dummy/{i}")
        # Mark all as complete, so they are evictable
        job.status = JobStatus.COMPLETE

    # We now have MAX_STORED_JOBS jobs, all completed
    assert len(manager._jobs) == MAX_STORED_JOBS

    # Let's set the first job to RUNNING (ANALYSING)
    first_job_id = next(iter(manager._jobs.keys()))
    manager._jobs[first_job_id].status = JobStatus.ANALYSING

    # Create one more job, which triggers eviction
    new_job = manager.create_job("http://dummy/new")

    # Ensure we still only have MAX_STORED_JOBS items
    assert len(manager._jobs) == MAX_STORED_JOBS

    # The first job which is analyzing should NOT have been evicted
    assert first_job_id in manager._jobs

def test_eviction_skips_when_all_running():
    manager = JobManager()

    for i in range(MAX_STORED_JOBS):
        job = manager.create_job(f"http://dummy/{i}")
        job.status = JobStatus.ANALYSING

    # Create one more job, triggers eviction but all are running
    new_job = manager.create_job("http://dummy/new")

    # Store size is allowed to grow past MAX_STORED_JOBS if all are running
    assert len(manager._jobs) == MAX_STORED_JOBS + 1
