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
