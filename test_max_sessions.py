import time
from archguard.dashboard._sessions import SESSION_STORE, _purge_expired_sessions, MAX_SESSIONS

# Ensure we start fresh
SESSION_STORE.clear()

# Add 501 sessions rapidly
for i in range(MAX_SESSIONS + 1):
    _purge_expired_sessions()
    SESSION_STORE[str(i)] = {"_ts": time.time(), "data": "dummy"}

if len(SESSION_STORE) == MAX_SESSIONS:
    print(f"SUCCESS: SESSION_STORE is at {MAX_SESSIONS} after adding {MAX_SESSIONS + 1} sessions.")
else:
    print(f"FAILED: SESSION_STORE is at {len(SESSION_STORE)}")
