import time
from archguard.utils.retry import with_retry

def test_retry_decorator_succeeds_after_failures(monkeypatch):
    # Reduce sleep to speed up test
    monkeypatch.setattr(time, "sleep", lambda x: None)
    
    call_count = 0
    
    @with_retry(max_attempts=3, retryable_exceptions=(ValueError,))
    def flaky_function():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("Failed!")
        return "Success"
        
    result = flaky_function()
    
    assert result == "Success"
    assert call_count == 3
