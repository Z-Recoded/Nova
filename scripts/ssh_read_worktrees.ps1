# ssh_read_worktrees.ps1
# Forced command for the Omen-to-Aero "worktrees" read-only SSH key
# (C:\ProgramData\ssh\administrators_authorized_keys, command= restriction).
# Invoked over SSH by nova_worktree_status.py's list_aero_worktrees() when
# nova_api.py is running on the Omen and needs the Aero's own open git
# worktrees for the real combined worktree browser view.
#
# Output format matches nova_worktree_status.py's existing Omen-side SSH
# fetch exactly (same "===MERGED===" / "===DATES===" markers), so the same
# parsing functions (_parse_worktree_porcelain, _parse_kv_lines) work
# unchanged regardless of which machine is being queried.

Set-Location "C:\Nova"
git worktree list --porcelain
Write-Output "===MERGED==="
git branch --merged master --format="%(refname:short)"
Write-Output "===DATES==="
git for-each-ref --format="%(refname:short)|%(committerdate:iso-strict)" refs/heads
