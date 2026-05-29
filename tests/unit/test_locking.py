import multiprocessing
import os
import signal
import sys
import time
from pathlib import Path

from archguard.cache.locking import file_lock

def acquire_and_hold(lock_path_str: str):
    lock_path = Path(lock_path_str)
    with file_lock(lock_path):
        # Hold the lock indefinitely so the parent can kill us
        while True:
            time.sleep(1)

def test_file_lock_released_on_sigkill(tmp_path):
    lock_path = tmp_path / "test.lock"
    
    # Start a subprocess that acquires the lock
    p = multiprocessing.Process(target=acquire_and_hold, args=(str(lock_path),))
    p.start()
    
    # Wait for the subprocess to actually acquire the lock
    time.sleep(1.0)
    
    # Kill the subprocess abruptly
    if sys.platform == "win32":
        p.terminate()  # Windows terminate is essentially SIGKILL
    else:
        os.kill(p.pid, signal.SIGKILL)
        
    p.join(timeout=2)
    
    # Verify we can acquire the lock without hanging
    acquired = False
    try:
        with file_lock(lock_path, timeout=5):
            acquired = True
    except TimeoutError:
        pass
        
    assert acquired, "Failed to acquire lock after subprocess was killed"


def test_file_lock_prevents_concurrent_access(tmp_path):
    import threading
    lock_path = str(tmp_path / "test.lock")
    results = []
    def worker():
        with file_lock(lock_path, timeout=5.0):
            results.append("start")
            time.sleep(0.1)
            results.append("end")
    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    # Verify no interleaving: each start is immediately followed by its end
    for i in range(0, len(results), 2):
        assert results[i] == "start"
        assert results[i+1] == "end"


def test_file_lock_timeout_raises(tmp_path):
    import pytest
    lock_path = str(tmp_path / "test.lock")
    with file_lock(lock_path):  # hold the lock
        with pytest.raises(TimeoutError):
            with file_lock(lock_path, timeout=0.1):  # should timeout
                pass
