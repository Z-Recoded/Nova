# launch_openwebui.ps1
# Sets the OpenAI-compatible environment Open WebUI needs to talk to
# nova_api.py (instead of raw Ollama), then starts Open WebUI.
# Called by start_nova.ps1 — nova_api.py must already be running on port 8000.

# DATABASE_URL is cleared here only (not globally) — a stray machine-wide
# Postgres URL makes Open WebUI try to load psycopg2 and crash otherwise.
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue

$env:Path = "C:\Users\marvi\.local\bin;$env:Path"
$env:OPENAI_API_BASE_URL = "http://localhost:8000/v1"
$env:OPENAI_API_KEY = "nova-local"
$env:ENABLE_OLLAMA_API = "false"

open-webui serve --host 0.0.0.0 --port 3000
