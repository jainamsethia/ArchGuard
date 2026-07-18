import asyncio
from fastapi.testclient import TestClient
import logging
import sys

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
from archguard.dashboard.app import app

client = TestClient(app)
response = client.post("/api/v1/advisor/ask", json={"question": "Test question"}, headers={"Authorization": "Bearer test"})
print("Status:", response.status_code)
