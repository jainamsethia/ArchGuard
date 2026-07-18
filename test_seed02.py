import requests
import time

s = requests.Session()
s.post('http://localhost:8765/api/auth/login', data={'token': 'demo-token-123'})

print("Submitting job 1...")
r = s.post('http://localhost:8765/api/v1/jobs', json={'repo_url': 'https://github.com/pypa/sampleproject'})
print(r.status_code, r.text)

print("Submitting job 2...")
r = s.post('http://localhost:8765/api/v1/jobs', json={'repo_url': 'https://github.com/psf/requests'})
print(r.status_code, r.text)

time.sleep(2)

print("\nTesting fabricated job_id...")
r1 = s.get('http://localhost:8765/api/v1/runs/latest?job_id=00000000-0000-0000-0000-123456789abc')
print(f"Status: {r1.status_code}")
print(f"Response: {r1.text}")

print("\nTesting no job_id...")
r2 = s.get('http://localhost:8765/api/v1/runs/latest')
print(f"Status: {r2.status_code}")
print(f"Response: {r2.text}")
