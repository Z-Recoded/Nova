# start_nova.ps1
# Launches nova_api.py and Open WebUI, each in its own terminal window.
# Run from a PowerShell prompt in C:\Nova: .\start_nova.ps1

$BOOT_WAIT_SECONDS = 5

# Step 1: Start nova_api.py (FastAPI backend) in a new window.
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command',
    'cd C:\Nova; nova-env\Scripts\python.exe -m uvicorn nova_api:app --host 0.0.0.0 --port 8000'
)

# Step 2: Wait for nova_api.py to finish booting — Open WebUI needs its
# /v1 routes reachable as soon as it starts.
Start-Sleep -Seconds $BOOT_WAIT_SECONDS

# Step 3: Start Open WebUI in a second new window, via its own script
# file rather than an inline -Command string — Start-Process's
# -ArgumentList silently drops embedded double quotes from long
# -Command strings, which breaks the env vars if written inline here.
Start-Process powershell -ArgumentList @(
    '-NoExit', '-File', 'C:\Nova\launch_openwebui.ps1'
)

Write-Host "nova_api launching on port 8000, Open WebUI launching on port 3000."
Write-Host "Give it a minute, then check http://localhost:3000"
