import requests
import time

s = requests.Session()
s.post('http://localhost:8765/api/auth/login', data={'token': 'demo-token-123'})

# We don't necessarily need to submit jobs through /api/v1/jobs, 
# because start_evolution just reads the audit log based on job_id.
# But let's create jobs to have distinct target paths, or we can just call start_evolution.
print("Submitting job A...")
rA = s.post('http://localhost:8765/api/v1/jobs', json={'github_url': 'https://github.com/pypa/sampleproject'})
job_a_id = rA.json().get('job_id')
print(f"Job A ID: {job_a_id}")

print("Submitting job B...")
rB = s.post('http://localhost:8765/api/v1/jobs', json={'github_url': 'https://github.com/psf/requests'})
job_b_id = rB.json().get('job_id')
print(f"Job B ID: {job_b_id}")

time.sleep(2)

print(f"Starting evolution for Job A: {job_a_id}...")
# start_evolution requires a body (EvolutionAnalyzeRequest)
body = {"max_commits": 5}
r_evoA = s.post(f'http://localhost:8765/api/v1/evolution/analyze?job_id={job_a_id}', json=body)
print(f"Evo A status: {r_evoA.status_code}")

print(f"Getting latest evolution for Job B: {job_b_id} (should be empty/available=False)...")
r_getB = s.get(f'http://localhost:8765/api/v1/evolution/latest?job_id={job_b_id}')
print(f"Get Evo B status: {r_getB.status_code}")
print(f"Get Evo B data: {r_getB.json()}")

if r_getB.json().get('available') is False:
    print("SUCCESS: Job B returned available: False")
else:
    print("FAIL: Job B returned data")
