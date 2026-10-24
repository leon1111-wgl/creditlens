$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& '.\.venv\Scripts\python.exe' scripts/download_data.py
if ($LASTEXITCODE -ne 0) { throw 'Dataset verification failed.' }
& '.\.venv\Scripts\python.exe' -m uvicorn creditlens.api:app --host 127.0.0.1 --port 8765
