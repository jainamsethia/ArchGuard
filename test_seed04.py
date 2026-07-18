import requests
import json
import time

s = requests.Session()

# 1. Create a job
r = s.post('http://127.0.0.1:8765/api/v1/jobs', 
           json={'github_url': 'https://github.com/pypa/sampleproject'},
           headers={'Authorization': 'Bearer demo-token-123'})
job_id = r.json().get('job_id')
print(f"Created job {job_id}")

# 2. Test SSE stream with query param token
print("Testing stream with ?token=demo-token-123")
url = f"http://127.0.0.1:8765/api/v1/jobs/{job_id}/stream?token=demo-token-123"
with s.get(url, stream=True) as response:
    print(f"Stream status: {response.status_code}")
    if response.status_code != 200:
        print("FAIL: Expected 200 OK")
        print(response.text)
    else:
        for line in response.iter_lines():
            if line:
                decoded = line.decode('utf-8')
                print(decoded)
                if '{"type": "done"}' in decoded:
                    print("Stream done.")
                    break
