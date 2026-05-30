import ipaddress
from urllib.parse import urlparse

def validate_webhook_url(url: str) -> None:
    """Validate that a webhook URL is safe to call.
    
    Prevents SSRF by rejecting:
    - Non-HTTPS URLs
    - Localhost / loopback addresses
    - Private IP ranges (RFC1918)
    """
    if not url:
        raise ValueError("Webhook URL cannot be empty")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Webhook URL must use HTTPS scheme")

    host = parsed.hostname
    if not host:
        raise ValueError("Invalid webhook URL: missing hostname")

    host_lower = host.lower()
    if host_lower == "localhost" or host_lower.startswith("127."):
        raise ValueError("Webhook URL cannot point to localhost or loopback address")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
        
    if ip and ip.is_private:
        raise ValueError(f"Webhook URL cannot point to a private IP address ({host})")
