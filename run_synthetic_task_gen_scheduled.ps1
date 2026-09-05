# run_synthetic_task_gen_scheduled.ps1
# Periodic wrapper for nova_synthetic_task_gen.py -- registered as a Windows
# Task Scheduler entry ("Nova Synthetic Task Gen", daily) so Phase 3's
# commit-back-translation SFT data keeps accumulating passively from Nova's
# own real ongoing development, without anyone needing to remember to run
# the batch by hand. Mirrors run_corrector_scheduled.ps1's shape exactly
# (same log-encoding discipline, same try/catch, same wrapper pattern) --
# see that script's own comments for why each piece is there.
#
# Deliberately capped at --limit 10 per run (~$0.50/day, confirmed live
# 2026-09-05 at $0.5387 for 10 commits) rather than --all-with-no-limit --
# this is meant to be a small, unattended, ongoing cost, not a way to
# silently re-run the full backlog. 43 real commits were still backlogged
# as of the first live verification run (211 curated of 254 candidates);
# at 10/day that clears in a few more days, then naturally throttles down
# to match real commit velocity once the backlog is gone.
#
# 2026-09-05 direction: Nova's coding-track model TESTING is on hold
# pending budget (see CLAUDE.md Phase 3.5 / ClickUp 86bbnbq0q, 86bbaph6w,
# 86baf4e70) -- this script is deliberately kept running anyway. It only
# spends modest Claude API cost (no GPU rental, no model-testing decision),
# and the (task, diff) pairs it produces are useful to whichever model
# eventually gets picked, so there is no reason to let the data go stale
# just because the model-selection question is paused.
#
# Run from a PowerShell prompt in C:\Nova to test manually: .\run_synthetic_task_gen_scheduled.ps1

$ErrorActionPreference = "Stop"

# Both lines below are required together -- confirmed live 2026-09-05.
# PYTHONIOENCODING makes python.exe actually emit UTF-8 bytes for non-ASCII
# text (Claude's own generated text routinely contains em-dashes/curly
# quotes) instead of silently falling back to the process's ANSI codepage
# once stdout is a redirected pipe rather than a real console. [Console]::
# OutputEncoding makes THIS PowerShell process decode that captured byte
# stream correctly before Add-Content re-encodes it -- without it, correct
# UTF-8 bytes get misread through the default codepage (437 for a fresh
# non-interactive powershell.exe, confirmed live) and Add-Content -Encoding
# utf8 then re-encodes the already-wrong string, producing "ΓÇö"-style
# double-mojibake. Either fix alone is insufficient; verified both ways.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

$LogPath = Join-Path $PSScriptRoot "logs\synthetic_task_gen_scheduled_run.log"

New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "logs") | Out-Null

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $LogPath -Value "`n=== $timestamp ===" -Encoding utf8

try {
    $output = & "$PSScriptRoot\nova-env\Scripts\python.exe" "$PSScriptRoot\nova_synthetic_task_gen.py" --all --limit 10 2>&1
    Add-Content -Path $LogPath -Value ($output | Out-String) -Encoding utf8
} catch {
    Add-Content -Path $LogPath -Value "ERROR: $_" -Encoding utf8
}
