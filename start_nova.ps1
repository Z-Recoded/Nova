# start_nova.ps1
# Launches nova_api.py and Open WebUI, each in its own terminal window.
# Run from a PowerShell prompt in C:\Nova: .\start_nova.ps1
#
# Safe to trigger automatically (e.g. Windows Task Scheduler at login/boot),
# not just from an interactive prompt:
#   - Idempotent - before starting either service, checks whether something
#     is already listening on its port and skips launching a duplicate
#     process if so. Re-running this script when everything is already up
#     is a safe no-op.
#   - -Silent switch - runs both services in hidden windows instead of
#     visible -NoExit ones, for headless/non-interactive runs where nobody
#     is watching (e.g. Task Scheduler at boot). Default behavior is
#     unchanged for manual interactive use.

param(
    [switch]$Silent
)

$BOOT_WAIT_SECONDS = 5
$NOVA_API_PORT = 8000
$OPEN_WEBUI_PORT = 3000

# Plain-English check for "is something already listening on this port?" -
# used before starting either service so re-running this script never
# spawns a duplicate process or errors out on an in-use port.
function Test-PortListening {
    param([int]$Port)
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $existing
}

# Step 1: Start nova_api.py (FastAPI backend) in a new window - unless
# something is already listening on its port, in which case skip it and
# say so, rather than launching a second competing instance.
$novaApiAlreadyRunning = Test-PortListening -Port $NOVA_API_PORT

if ($novaApiAlreadyRunning) {
    Write-Host "nova_api.py already listening on port $NOVA_API_PORT - skipping launch."
} else {
    if ($Silent) {
        # Headless: hidden window, no -NoExit. Nobody is watching this at
        # boot, and the shell only needs to stay alive as long as uvicorn
        # does - it doesn't need to linger open for a human to read.
        Start-Process powershell -WindowStyle Hidden -ArgumentList @(
            '-Command',
            "cd C:\Nova; nova-env\Scripts\python.exe -m uvicorn nova_api:app --host 0.0.0.0 --port $NOVA_API_PORT"
        )
    } else {
        Start-Process powershell -ArgumentList @(
            '-NoExit', '-Command',
            "cd C:\Nova; nova-env\Scripts\python.exe -m uvicorn nova_api:app --host 0.0.0.0 --port $NOVA_API_PORT"
        )
    }

    # Step 2: Wait for nova_api.py to finish booting - Open WebUI needs its
    # /v1 routes reachable as soon as it starts. Only needed when we just
    # started it fresh; if it was already up, there's nothing to wait on.
    Start-Sleep -Seconds $BOOT_WAIT_SECONDS
}

# Step 3: Start Open WebUI in a second new window, via its own script
# file rather than an inline -Command string - Start-Process's
# -ArgumentList silently drops embedded double quotes from long
# -Command strings, which breaks the env vars if written inline here.
# Skipped the same way as nova_api.py if it's already listening - and
# launch_openwebui.ps1 re-checks this itself too, in case it's ever
# triggered directly instead of through this script.
if (Test-PortListening -Port $OPEN_WEBUI_PORT) {
    Write-Host "Open WebUI already listening on port $OPEN_WEBUI_PORT - skipping launch."
} else {
    if ($Silent) {
        Start-Process powershell -WindowStyle Hidden -ArgumentList @(
            '-File', 'C:\Nova\launch_openwebui.ps1'
        )
    } else {
        Start-Process powershell -ArgumentList @(
            '-NoExit', '-File', 'C:\Nova\launch_openwebui.ps1'
        )
    }
}

Write-Host "nova_api on port $NOVA_API_PORT, Open WebUI on port $OPEN_WEBUI_PORT."
Write-Host "Give it a minute, then check http://localhost:$OPEN_WEBUI_PORT"
