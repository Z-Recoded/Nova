# launch_openwebui.ps1
# Sets the OpenAI-compatible environment Open WebUI needs to talk to
# nova_api.py (instead of raw Ollama), then starts Open WebUI.
# Called by start_nova.ps1 - nova_api.py must already be running on port 8000.
#
# Also safe to trigger directly (e.g. from Task Scheduler) rather than only
# through start_nova.ps1 - re-checks port 3000 itself before launching so it
# never starts a second competing Open WebUI process.

$OPEN_WEBUI_PORT = 3000

# Plain-English check for "is something already listening on this port?" -
# mirrors the same check in start_nova.ps1 so this script is also a safe
# no-op if something is already serving on port 3000.
function Test-PortListening {
    param([int]$Port)
    $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return $null -ne $existing
}

if (Test-PortListening -Port $OPEN_WEBUI_PORT) {
    Write-Host "Open WebUI already listening on port $OPEN_WEBUI_PORT - skipping launch."
    exit 0
}

# DATABASE_URL is cleared here only (not globally) - a stray machine-wide
# Postgres URL makes Open WebUI try to load psycopg2 and crash otherwise.
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue

$env:Path = "C:\Users\marvi\.local\bin;$env:Path"
$env:OPENAI_API_BASE_URL = "http://localhost:8000/v1"
$env:OPENAI_API_KEY = "nova-local"
$env:ENABLE_OLLAMA_API = "false"

open-webui serve --host 0.0.0.0 --port $OPEN_WEBUI_PORT
