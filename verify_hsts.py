from fastapi.testclient import TestClient
from archguard.dashboard.app import app
import os

print('Testing without ENVIRONMENT...')
os.environ.pop('ENVIRONMENT', None)
client = TestClient(app)
res = client.get('/health')
print('HSTS present:', 'Strict-Transport-Security' in res.headers)

print('Testing with ENVIRONMENT=production...')
os.environ['ENVIRONMENT'] = 'production'
res = client.get('/health')
print('HSTS present:', 'Strict-Transport-Security' in res.headers)
print('HSTS value:', res.headers.get('Strict-Transport-Security'))

