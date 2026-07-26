# ssh_read_training_flags.ps1
# Forced command for the Omen-to-Aero "trainingdata" read-only SSH key
# (C:\ProgramData\ssh\administrators_authorized_keys, command= restriction).
# Invoked over SSH by nova_training_data_status.py's
# read_aero_training_flags() when nova_api.py is running on the Omen and
# needs the Aero's own logs/training_flags.jsonl for the real combined
# DPO-pair count (fixes /training-data-status showing 0/100 on the Omen
# instead of the Aero's real count).
#
# Same raw-byte-passthrough fix as ssh_read_agent_log.ps1 (2026-07-25):
# Get-Content | Write-Output re-encodes through PowerShell's own text
# pipeline using the console's active codepage, which corrupts real
# non-ASCII bytes in transit (training_flags.jsonl's correction text can
# contain the same kind of characters that broke agent_log.jsonl). Reading
# and writing raw bytes directly bypasses that entirely.

$bytes = [System.IO.File]::ReadAllBytes("C:\Nova\logs\training_flags.jsonl")
$stdout = [Console]::OpenStandardOutput()
$stdout.Write($bytes, 0, $bytes.Length)
$stdout.Flush()
