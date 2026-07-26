# ssh_read_agent_log.ps1
# Forced command for the Omen-to-Aero "agentlog" read-only SSH key
# (C:\ProgramData\ssh\administrators_authorized_keys, command= restriction).
# Invoked over SSH by nova_agent_log_status.py's read_aero_agent_log() when
# nova_api.py is running on the Omen and needs the Aero's own
# logs/agent_log.jsonl for the real combined Qwen3 swap-trigger count.
#
# Deliberately a plain script file, not an inline command= string in
# authorized_keys -- avoids the fragile double-layer quoting (authorized_keys
# parser, then cmd.exe/powershell.exe parser) that an inline multi-statement
# command would need.
#
# Real bug found live: Get-Content | Write-Output routes the file through
# PowerShell's own string pipeline, which re-encodes using the console's
# active codepage -- NOT necessarily UTF-8 -- before it ever reaches SSH's
# stdout. agent_log.jsonl has real non-ASCII bytes (em-dashes, etc.) that
# got mangled in transit, breaking Python's UTF-8 decode on the Omen side
# with a real UnicodeDecodeError. Fixed by reading and writing raw bytes
# directly, bypassing PowerShell's text pipeline (and its encoding
# assumptions) entirely -- whatever encoding the file was actually written
# in survives the SSH round-trip unchanged.

$bytes = [System.IO.File]::ReadAllBytes("C:\Nova\logs\agent_log.jsonl")
$stdout = [Console]::OpenStandardOutput()
$stdout.Write($bytes, 0, $bytes.Length)
$stdout.Flush()
