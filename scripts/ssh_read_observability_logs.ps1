# ssh_read_observability_logs.ps1
# Forced command for the Omen-to-Aero "observabilitylogs" read-only SSH key
# (C:\ProgramData\ssh\administrators_authorized_keys, command= restriction).
# Invoked over SSH by nova_observability_status.py's read_aero_observability_logs()
# when nova_api.py is running on the Omen and needs the Aero's own
# logs/guard_events_log.jsonl + logs/ground_truth_gate_log.jsonl for the real
# combined /observability/per-model and /observability/failure-frequency data
# -- almost all real coding-agent activity happens on the Aero (interactive
# lane + local nova_coding_eval.py runs), so the Omen's own copies of these
# files are near-empty or missing entirely.
#
# Bundles both files into one JSON response (one SSH round-trip instead of
# two) since they're always consumed together for this exact purpose, unlike
# ssh_read_agent_log.ps1's single-file scope.
#
# Same real encoding gotcha ssh_read_agent_log.ps1 already found and fixed:
# Get-Content | Write-Output re-encodes through PowerShell's console codepage,
# corrupting non-ASCII bytes in transit. Reading each file as raw bytes and
# decoding as UTF-8 explicitly (matching how these JSONL files are actually
# written -- json.dumps(..., ensure_ascii=False)) avoids that entirely, and
# the final JSON envelope is itself written as raw UTF-8 bytes straight to
# stdout, bypassing the text pipeline for the whole round-trip.

function Read-Utf8OrEmpty([string]$path) {
    if (-not (Test-Path $path)) {
        return ""
    }
    $bytes = [System.IO.File]::ReadAllBytes($path)
    return [System.Text.Encoding]::UTF8.GetString($bytes)
}

$guardEvents = Read-Utf8OrEmpty "C:\Nova\logs\guard_events_log.jsonl"
$groundTruthGate = Read-Utf8OrEmpty "C:\Nova\logs\ground_truth_gate_log.jsonl"

$payload = @{
    guard_events       = $guardEvents
    ground_truth_gate  = $groundTruthGate
} | ConvertTo-Json -Compress

$bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
$stdout = [Console]::OpenStandardOutput()
$stdout.Write($bytes, 0, $bytes.Length)
$stdout.Flush()
