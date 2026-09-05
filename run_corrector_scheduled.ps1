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
#
# 2026-09-05: found + fixed a second, related encoding bug while building
# run_synthetic_task_gen_scheduled.ps1's own wrapper -- a fresh non-
# interactive powershell.exe (exactly how Task Scheduler launches this
# script) decodes a captured native command's stdout using the OEM
# codepage (437, confirmed live) rather than UTF-8, so any real em-dash/
# curly-quote in flagged lore text would have come through this log as
# mojibake even though Add-Content -Encoding utf8 was already correct on
# the write side. Both lines below are required together: PYTHONIOENCODING
# makes python.exe actually emit UTF-8 bytes once stdout is a redirected
# pipe (it silently falls back to the ANSI codepage otherwise), and
# [Console]::OutputEncoding makes this process decode that byte stream
# correctly before Add-Content re-encodes it. Either alone is insufficient
# -- verified both ways on the sibling wrapper before applying here.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

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
