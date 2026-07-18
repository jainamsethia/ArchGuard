import asyncio
from fastapi.testclient import TestClient
import logging
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from archguard.dashboard.app import app

client = TestClient(app)
response = client.get("/api/v1/auth/status", headers={"X-Forwarded-For": "203.0.113.1, 198.51.100.1"})
print("Status:", response.status_code)
print("Headers:", response.headers)
