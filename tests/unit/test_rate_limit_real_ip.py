import pytest
from unittest.mock import patch, MagicMock
from fastapi import Request
from archguard.dashboard._rate_limit import rate_limiter


def test_rate_limiter_calls_real_client_ip_not_request_client_host():
    """Regression for HIGH-01: rate_limiter must call _real_client_ip(), not request.client.host."""
    # Arrange
    with patch("archguard.dashboard._rate_limit._real_client_ip", return_value="1.2.3.4") as mock_rci, \
         patch("archguard.dashboard._rate_limit.RATE_LIMITS", {}), \
         patch("archguard.dashboard._rate_limit.RATE_LIMIT_WINDOW", 60), \
         patch("archguard.dashboard._rate_limit.RATE_LIMIT_MAX_REQUESTS", 100):
        req = MagicMock(spec=Request)
        # Act
        try:
            rate_limiter(req)
        except Exception:
            pass  # 429 or pass — we only care that _real_client_ip was called
        # Assert
        mock_rci.assert_called_once_with(req)
