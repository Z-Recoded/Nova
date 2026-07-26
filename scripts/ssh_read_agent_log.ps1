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

Get-Content -Raw -Path "C:\Nova\logs\agent_log.jsonl"
