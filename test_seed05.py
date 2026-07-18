import requests

s = requests.Session()

print("Submitting job...")
rA = s.post('http://localhost:8765/api/v1/jobs', 
            json={'github_url': 'https://github.com/pypa/sampleproject'},
            headers={'Authorization': 'Bearer master-token-123'})
data = rA.json()
job_id = data.get('job_id')
stream_url = data.get('stream_url')
print(f"Job ID: {job_id}")
print(f"Stream URL: {stream_url}")

# Verify token is present and distinct
token = stream_url.split('token=')[1]
print(f"Extracted token: {token}")
assert token != "master-token-123", "Token should not be the master token"

# Test 1st use (should be valid)
print("Testing 1st use (validating auth but breaking stream quickly by closing connection)...")
r_stream = s.get(f'http://localhost:8765{stream_url}', stream=True)
print(f"1st use status: {r_stream.status_code}")
if r_stream.status_code == 200:
    print("1st use passed auth.")
else:
    print(f"FAIL: 1st use returned {r_stream.status_code}")
r_stream.close()

# Test 2nd use (should be rejected since token is single-use)
print("Testing 2nd use (should be rejected)...")
r_stream2 = s.get(f'http://localhost:8765{stream_url}', stream=True)
print(f"2nd use status: {r_stream2.status_code}")
if r_stream2.status_code == 401:
    print("SUCCESS: 2nd use was correctly rejected.")
else:
    print(f"FAIL: 2nd use returned {r_stream2.status_code}, expected 401")
    print(r_stream2.text)
r_stream2.close()
