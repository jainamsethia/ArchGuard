import pytest

from archguard.alerting.trend_detector import TrendAlert
from archguard.alerting.webhooks import send_slack_alert
from archguard.utils.url_validator import validate_webhook_url


def test_valid_webhook_url():
    # Should not raise
    validate_webhook_url("https://example.com/webhook")


def test_invalid_scheme():
    with pytest.raises(ValueError, match="must use HTTPS scheme"):
        validate_webhook_url("http://hooks.slack.com/services/T0000/B0000")


def test_invalid_private_ip():
    with pytest.raises(ValueError, match="cannot point to a private IP address"):
        validate_webhook_url("https://192.168.1.100/webhook")

    with pytest.raises(ValueError, match="cannot point to a private IP address"):
        validate_webhook_url("https://10.0.0.5/webhook")


def test_invalid_localhost():
    with pytest.raises(ValueError, match="cannot point to localhost or loopback"):
        validate_webhook_url("https://localhost:8080/webhook")

    with pytest.raises(ValueError, match="cannot point to localhost or loopback"):
        validate_webhook_url("https://127.0.0.1/webhook")


@pytest.mark.asyncio
async def test_send_slack_alert_validates_url():
    alerts = [
        TrendAlert(
            message="Test",
            metric="test",
            module="test",
            direction="up",
            delta=0.5,
            window=10,
        )
    ]
    with pytest.raises(ValueError, match="must use HTTPS scheme"):
        await send_slack_alert("http://example.com", alerts)
