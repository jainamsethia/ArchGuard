$ErrorActionPreference = "Stop"

Write-Host "Creating test repo..."
$TEST_REPO = "tests\fixtures\final_verification"
if (Test-Path $TEST_REPO) { Remove-Item -Recurse -Force $TEST_REPO }
New-Item -ItemType Directory -Path $TEST_REPO | Out-Null
Set-Location $TEST_REPO

Write-Host "Initializing git repo..."
git init
Set-Content -Path main.py -Value "print('hello')" -Encoding UTF8
git add main.py
git commit -m "Initial commit"

Write-Host "Testing 1. init"
..\..\..\test_venv_all\Scripts\archguard.exe init --llm-init
# We need to answer the profile prompt. Actually we can use --wizard or just pipe.
# Let's bypass interactive by creating .archguard.yml directly or using a non-interactive mode. Wait, init doesn't have non-interactive flag for profile.
# We'll just echo 1 | archguard init
cmd.exe /c "echo 1 | ..\..\..\test_venv_all\Scripts\archguard.exe init"
if ($LASTEXITCODE -ne 0) { throw "init failed" }

Write-Host "Testing 2. analyze"
..\..\..\test_venv_all\Scripts\archguard.exe analyze
if ($LASTEXITCODE -ne 0) { throw "analyze failed" }

Write-Host "Testing 3. report"
..\..\..\test_venv_all\Scripts\archguard.exe report
if ($LASTEXITCODE -ne 0) { throw "report failed" }

Write-Host "Testing 4. diff"
Add-Content -Path main.py -Value "print('world')" -Encoding UTF8
git add main.py
git commit -m "Second commit"
..\..\..\test_venv_all\Scripts\archguard.exe analyze
..\..\..\test_venv_all\Scripts\archguard.exe diff
if ($LASTEXITCODE -ne 0) { throw "diff failed" }

Write-Host "Testing 5. history"
..\..\..\test_venv_all\Scripts\archguard.exe history
if ($LASTEXITCODE -ne 0) { throw "history failed" }

Write-Host "Testing 6. fitness"
..\..\..\test_venv_all\Scripts\archguard.exe fitness
if ($LASTEXITCODE -ne 0) { throw "fitness failed" }

Write-Host "Testing 7. cache-check"
..\..\..\test_venv_all\Scripts\archguard.exe cache-check
if ($LASTEXITCODE -ne 0) { throw "cache-check failed" }

Write-Host "Testing 8. history-analyze"
..\..\..\test_venv_all\Scripts\archguard.exe history-analyze
if ($LASTEXITCODE -ne 0) { throw "history-analyze failed" }

Write-Host "Testing 9. status"
..\..\..\test_venv_all\Scripts\archguard.exe status
if ($LASTEXITCODE -ne 0) { throw "status failed" }

Write-Host "Testing 10. suppress"
..\..\..\test_venv_all\Scripts\archguard.exe suppress list
if ($LASTEXITCODE -ne 0) { throw "suppress failed" }

Write-Host "Testing 11. profiles"
..\..\..\test_venv_all\Scripts\archguard.exe profiles list
if ($LASTEXITCODE -ne 0) { throw "profiles failed" }

Write-Host "Testing 12. contract"
..\..\..\test_venv_all\Scripts\archguard.exe contract list-pending
if ($LASTEXITCODE -ne 0) { throw "contract failed" }

Write-Host "ALL TESTS PASSED!"
Set-Location "..\..\.."
