# ssh_patch_training_flags.ps1
# Forced command for the Omen-to-Aero "trainingflags-write" SSH key
# (C:\ProgramData\ssh\administrators_authorized_keys, command= restriction).
# The one WRITE-capable key in this bridge -- every other key here
# (ssh_read_agent_log.ps1, ssh_read_worktrees.ps1, ssh_read_training_flags.ps1)
# is read-only. Invoked by nova_training_data_status.dispatch_remote_patch()
# when a blend_flag/dpo_verify card being decided from the Omen-hosted
# Controller actually lives in the Aero's own training_flags.jsonl.
#
# Deliberately still narrow despite writing: the client's SSH command
# argument is ignored, same as every other key here -- the only real input
# is stdin (a small JSON request), and this script only ever touches
# C:\Nova\logs\training_flags.jsonl, one specific field (correction or
# verification_status) at a time, gated by the same index/timestamp safety
# check nova_training_flags_patch.patch_training_flags_entry() uses for the
# local-machine case.
#
# Pure PowerShell, no external process spawn (2026-07-26 rewrite): the
# first version shelled out to python.exe (nova_patch_training_flags_cli.py)
# the same way the read scripts avoid entirely. Real, live failure found
# during verification: SSH public-key sessions on Windows authenticate with
# a "network logon" token, and Windows refuses to use that token type for
# CreateProcess at all -- confirmed by adding CreateNoWindow/
# RedirectStandardError to rule out the console-window-station theory
# first, which did NOT fix it, isolating the real cause to process
# creation itself, not window handling. The other three forced scripts
# never hit this because none of them spawn a child process. This version
# reimplements patch_training_flags_entry()'s exact logic natively --
# same index/timestamp check, same two allowed fields, same one hardcoded
# path. Keep the Python and PowerShell versions in sync by hand if that
# logic ever changes; nova_training_flags_patch.py is still the one used
# for local-machine patches and for the Aero->Omen leg (Linux sshd has no
# equivalent restriction, so nova_patch_training_flags_cli.py still runs
# there unchanged).
#
# Raw-byte passthrough for the request read, UTF8-no-BOM for the write --
# same encoding discipline as the read scripts (ssh_read_agent_log.ps1):
# Set-Content/Out-File -Encoding utf8 adds a BOM on a full rewrite on this
# machine, which would corrupt the file for the Python readers that assume
# plain UTF-8.

$TRAINING_FLAGS_PATH = "C:\Nova\logs\training_flags.jsonl"

function Write-JsonResponse {
    param($ResponseObject)
    $json = $ResponseObject | ConvertTo-Json -Compress -Depth 10
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $stdout = [Console]::OpenStandardOutput()
    $stdout.Write($bytes, 0, $bytes.Length)
    $stdout.Flush()
}

$stdin = [Console]::OpenStandardInput()
$inputStream = New-Object System.IO.MemoryStream
$stdin.CopyTo($inputStream)
$requestText = [System.Text.Encoding]::UTF8.GetString($inputStream.ToArray())

try {
    $req = $requestText | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-JsonResponse @{ ok = $false; status = 422; detail = "Malformed patch request: $($_.Exception.Message)" }
    exit
}

$kind = $req.kind
if ($kind -ne "blend_flag" -and $kind -ne "dpo_verify") {
    Write-JsonResponse @{ ok = $false; status = 422; detail = "Unknown kind '$kind' for a training_flags.jsonl patch" }
    exit
}
if ($kind -eq "dpo_verify" -and $req.verification_status -ne "confirmed_good" -and $req.verification_status -ne "needs_rework") {
    Write-JsonResponse @{ ok = $false; status = 422; detail = "verification_status must be 'confirmed_good' or 'needs_rework'" }
    exit
}

$index = [int]$req.index
$expectedTimestamp = [string]$req.expected_timestamp

# Read every line, silently skipping blank/malformed ones -- same
# convention as nova_training_flags_patch._read_jsonl().
$entries = @()
if (Test-Path $TRAINING_FLAGS_PATH) {
    foreach ($line in Get-Content -Path $TRAINING_FLAGS_PATH -Encoding UTF8) {
        if ($line.Trim().Length -eq 0) { continue }
        try {
            $entries += , ($line | ConvertFrom-Json -ErrorAction Stop)
        } catch {
            continue
        }
    }
}

if ($index -ge $entries.Count -or $entries[$index].timestamp -ne $expectedTimestamp) {
    Write-JsonResponse @{
        ok     = $false
        status = 409
        detail = "This entry's position/timestamp no longer matches -- training_flags.jsonl changed since this card was loaded. Reload the queue and try again."
    }
    exit
}

if ($kind -eq "blend_flag") {
    $correction = if ($null -ne $req.correction) { $req.correction } else { "" }
    $entries[$index] | Add-Member -MemberType NoteProperty -Name "correction" -Value $correction -Force
} else {
    $entries[$index] | Add-Member -MemberType NoteProperty -Name "verification_status" -Value $req.verification_status -Force
}

$outLines = $entries | ForEach-Object { $_ | ConvertTo-Json -Compress -Depth 10 }
[System.IO.File]::WriteAllLines($TRAINING_FLAGS_PATH, $outLines, (New-Object System.Text.UTF8Encoding($false)))

Write-JsonResponse @{ ok = $true; entry = $entries[$index] }
