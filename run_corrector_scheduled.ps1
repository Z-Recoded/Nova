# run_corrector_scheduled.ps1
# Periodic wrapper for nova_corrector.py -- registered as a Windows Task
# Scheduler entry ("Nova DPO Corrector", every 2 hours) so real flagged
# blend entries in training_flags.jsonl get corrected automatically
# instead of requiring someone to remember to run the script by hand.
# Matches nova_scheduled_dispatch.py's own 2-hour cadence on the Omen --
# same reasoning: a real, cheap periodic check, not a tight polling loop.
#
# Safe to trigger unattended:
#   - nova_corrector.py itself is already idempotent (only processes
#     entries where correction == "", exits immediately with "No
#     uncorrected entries found." when there's nothing new) -- most runs
#     of this wrapper do real work only when a real blend was actually
#     flagged since the last run.
#   - Each correction is now saved individually (2026-07-22 fix,
#     nova_corrector.py) -- a crash mid-run no longer loses already-
#     completed corrections the way an earlier real run did.
#   - ANTHROPIC_API_KEY comes from .env via nova_corrector.py's own
#     load_dotenv() call -- no env var injection needed here.
#
# Run from a PowerShell prompt in C:\Nova to test manually: .\run_corrector_scheduled.ps1
#
# Log writes go through Add-Content -Encoding utf8 explicitly -- Tee-Object's
# default encoding in Windows PowerShell 5.1 is ambiguous and produced a real,
# unreadable UTF-16-as-single-byte garbled log on first live test (confirmed:
# "N o   u n c o r r e c t e d" instead of "No uncorrected"), the same class
# of PowerShell text-encoding gotcha already known on this machine.

$ErrorActionPreference = "Stop"
$LogPath = Join-Path $PSScriptRoot "logs\corrector_scheduled_run.log"

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "logs") | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogPath -Value "`n=== $timestamp ===" -Encoding utf8

try {
    $output = & "$PSScriptRoot\nova-env\Scripts\python.exe" "$PSScriptRoot\nova_corrector.py" 2>&1
    Add-Content -Path $LogPath -Value ($output | Out-String) -Encoding utf8
} catch {
    Add-Content -Path $LogPath -Value "ERROR: $_" -Encoding utf8
}
