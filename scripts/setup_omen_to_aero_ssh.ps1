# setup_omen_to_aero_ssh.ps1
# One-time elevated setup for command-restricted Omen -> Aero SSH access
# (86bb3cey2 / 86bb3ceyc's "omen_only" gap). Run this in an Administrator
# PowerShell window on the Aero. Safe to re-run -- every step is idempotent.
#
# What this does:
#   1. Installs the OpenSSH Server Windows feature if not already present.
#   2. Starts sshd and sets it to auto-start.
#   3. Adds a Windows Firewall rule that ONLY allows inbound SSH from
#      Tailscale's own address range (100.64.0.0/10) -- not the LAN, not
#      the public internet. A device on the same WiFi that isn't on the
#      tailnet cannot even attempt to connect.
#   4. Restricts sshd itself to listen ONLY on the Tailscale interface
#      (belt-and-suspenders alongside the firewall rule -- even if the
#      firewall rule were ever misconfigured, sshd wouldn't be listening
#      on the LAN-facing NIC at all).
#   5. Installs three read-only, command-restricted keys into
#      administrators_authorized_keys (required for admin-group accounts
#      like this one -- the per-user .ssh\authorized_keys file is ignored
#      for accounts in the Administrators group). Each key can run exactly
#      one whitelisted script (C:\Nova\scripts\ssh_read_*.ps1) and nothing
#      else -- no port/X11/agent forwarding, no interactive shell.
#   6. Installs a fourth, WRITE-capable key (trainingflags-write) -- OPT-IN,
#      requires its pubkey to be pasted into this script first (see step 6's
#      own output for exact instructions). Restricted to one script
#      (ssh_patch_training_flags.ps1) that can only patch one file
#      (training_flags.jsonl), one field at a time, gated by the same
#      index/timestamp check the local decide route uses.
#   8. Installs a fifth, relay-only key (worktree-pr) -- also OPT-IN. Never
#      runs git/gh itself (SSH sessions here can't spawn any process at
#      all, confirmed during 86bb3ceyj); only relays a create-PR request
#      to this machine's own already-running nova_api.py, which does the
#      real work (86bb3ceyf).
#
# 2026-07-26 update: added the third ("trainingdata", read-only) key --
# fixes /training-data-status showing 0/100 on the Omen (it has no
# training_flags.jsonl of its own) instead of the Aero's real count. Also
# added the fourth ("trainingflags-write") key so the Controller's
# blend_flag/dpo_verify swipe cards can actually be decided from the
# Omen-hosted Controller, not just viewed. And the fifth ("worktree-pr")
# key, so the diff-preview-and-merge feature (86bb3ceyf) can push a real
# GitHub PR for a dispatched task from the Omen-hosted Controller. Run
# this again after pulling that change; the first four steps are
# unchanged and will just report "already done."
#
# 2026-08-05 update: added the sixth ("diff", read-only) key -- backs the
# Observability dashboard's cross-machine "View diff" coverage
# (86bb7pb6t follow-up). Unlike worktree-pr, this one runs git DIRECTLY
# (confirmed live this session that a forced Omen-to-Aero SSH command can
# run git.exe successfully -- see ssh_read_aero_diff.ps1's own docstring),
# so it doesn't depend on nova_api.py actually running on the Aero.

$ErrorActionPreference = "Stop"

Write-Output "1. Checking OpenSSH Server capability..."
$cap = Get-WindowsCapability -Online -Name "OpenSSH.Server*"
if ($cap.State -ne "Installed") {
    Add-WindowsCapability -Online -Name $cap.Name
    Write-Output "   Installed."
} else {
    Write-Output "   Already installed."
}

Write-Output "2. Starting sshd and setting it to auto-start..."
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
Write-Output "   Done."

Write-Output "3. Adding Tailscale-only firewall rule..."
$ruleName = "Nova SSH (Tailscale, Omen callback)"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort 22 `
        -RemoteAddress "100.64.0.0/10" -Action Allow | Out-Null
    Write-Output "   Created."
} else {
    Write-Output "   Already exists."
}

Write-Output "4. Restricting sshd to listen only on the Tailscale interface..."
$tailscaleIp = (Get-NetIPAddress -InterfaceAlias "Tailscale*" -AddressFamily IPv4 -ErrorAction SilentlyContinue).IPAddress
if (-not $tailscaleIp) {
    Write-Output "   WARNING: could not auto-detect the Tailscale interface IP -- skipping ListenAddress restriction."
    Write-Output "   sshd will listen on all interfaces; the firewall rule from step 3 is still in effect."
} else {
    $sshdConfigPath = "C:\ProgramData\ssh\sshd_config"
    $lines = Get-Content $sshdConfigPath
    $listenLine = "ListenAddress $tailscaleIp"
    if ($lines -contains $listenLine) {
        Write-Output "   Already restricted to $tailscaleIp."
    } else {
        # ListenAddress is a global directive -- it MUST appear before any
        # "Match" block, or sshd refuses to start (Match blocks only permit
        # a specific keyword allowlist and ListenAddress isn't in it). A
        # naive append lands after Windows' default "Match Group
        # administrators" block and breaks the service -- confirmed live
        # during real setup, not a hypothetical edge case.
        $matchLineNumber = ($lines | Select-String -Pattern '^\s*Match\s' | Select-Object -First 1).LineNumber
        if ($matchLineNumber) {
            $before = $lines[0..($matchLineNumber - 2)]
            $after = $lines[($matchLineNumber - 1)..($lines.Count - 1)]
            $newLines = $before + @($listenLine, "") + $after
        } else {
            $newLines = $lines + @($listenLine)
        }
        Set-Content -Path $sshdConfigPath -Value $newLines -Encoding ascii
        Write-Output "   Added 'ListenAddress $tailscaleIp' to sshd_config (before the Match block)."
    }
}

Write-Output "5. Installing the four read-only, command-restricted keys..."
$keysDir = "C:\ProgramData\ssh"
$keysFile = Join-Path $keysDir "administrators_authorized_keys"

$agentLogKey = 'command="powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Nova\scripts\ssh_read_agent_log.ps1",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINYwrlkDv4uEgNbLPuQKiNl2Iu84hJIumRucCi/mVLaN omen-to-aero-agentlog-readonly'
$worktreesKey = 'command="powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Nova\scripts\ssh_read_worktrees.ps1",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINk1t+7xbCyLgOa+Y2i7Tp1PSkZUiaKAurysPbG/sa51 omen-to-aero-worktrees-readonly'
$trainingDataKey = 'command="powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Nova\scripts\ssh_read_training_flags.ps1",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINbdhHC/7txt15uh3BTTe5ZkLvd+3IxG/bVB9gudtrkd omen-to-aero-trainingdata-readonly'
# 2026-08-06 addition -- backs the /observability/per-model and
# /observability/failure-frequency cross-machine fix (nova_observability_status.py).
# Same auto-installed, read-only class as the three above -- just reads
# guard_events_log.jsonl/ground_truth_gate_log.jsonl, bundled into one JSON
# response (see ssh_read_observability_logs.ps1).
$observabilityLogsKey = 'command="powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Nova\scripts\ssh_read_observability_logs.ps1",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHa+LqduVmRz16JApQXeUNJpefj6w9bqEgh+9N8ZWrq/ omen-to-aero-observabilitylogs-readonly'

$content = @()
if (Test-Path $keysFile) { $content = Get-Content $keysFile }
if ($content -notcontains $agentLogKey) { $content += $agentLogKey }
if ($content -notcontains $worktreesKey) { $content += $worktreesKey }
if ($content -notcontains $trainingDataKey) { $content += $trainingDataKey }
if ($content -notcontains $observabilityLogsKey) { $content += $observabilityLogsKey }
Set-Content -Path $keysFile -Value $content -Encoding ascii

# Required by Windows OpenSSH: administrators_authorized_keys must be
# readable/writable ONLY by SYSTEM and Administrators, or sshd silently
# ignores every key in it (a well-documented Windows-specific gotcha).
icacls.exe $keysFile /inheritance:r | Out-Null
icacls.exe $keysFile /grant "Administrators:F" | Out-Null
icacls.exe $keysFile /grant "SYSTEM:F" | Out-Null
Write-Output "   Installed and permissions locked down."

Write-Output "6. Installing the fourth key (WRITE-capable -- trainingflags-write)..."
Write-Output "   This one is opt-in, not auto-installed like the three read-only keys above:"
Write-Output "   it can PATCH training_flags.jsonl (one field, index/timestamp-checked -- see"
Write-Output "   nova_training_flags_patch.py), a real step up from pure read access, so its"
Write-Output "   keypair generation was deliberately left for Marvin to run explicitly rather"
Write-Output "   than auto-generated."
$trainingFlagsWriteKey = 'command="powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Nova\scripts\ssh_patch_training_flags.ps1",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFlxTYkVkiIIYI2bpb2UlKkM+u84noBOV0Zc9ShIpGKi omen-to-aero-trainingflags-write'
if ($trainingFlagsWriteKey -match '<PASTE_PUBKEY_HERE>') {
    Write-Output "   SKIPPED -- placeholder pubkey not filled in yet. To enable the write bridge:"
    Write-Output "     1. On the Omen: ssh-keygen -t ed25519 -N '' -f ~/.ssh/aero_keys/id_ed25519_aero_trainingflags_write"
    Write-Output "     2. Copy its public half (cat ~/.ssh/aero_keys/id_ed25519_aero_trainingflags_write.pub)"
    Write-Output "     3. Paste it into this script in place of <PASTE_PUBKEY_HERE>, then re-run"
    Write-Output "   Until then, /label-queue/{kind}/{id}/decide falls back to a 503 for any"
    Write-Output "   blend_flag/dpo_verify card whose entry lives on the other machine."
} else {
    $content = Get-Content $keysFile
    if ($content -notcontains $trainingFlagsWriteKey) {
        $content += $trainingFlagsWriteKey
        Set-Content -Path $keysFile -Value $content -Encoding ascii
        icacls.exe $keysFile /inheritance:r | Out-Null
        icacls.exe $keysFile /grant "Administrators:F" | Out-Null
        icacls.exe $keysFile /grant "SYSTEM:F" | Out-Null
        Write-Output "   Installed."
    } else {
        Write-Output "   Already installed."
    }
}

Write-Output "8. Installing the fifth key (relay-only -- worktree-pr)..."
Write-Output "   Also opt-in. Unlike the write key above, this one never touches"
Write-Output "   training_flags.jsonl or any file directly -- it relays a create-PR request"
Write-Output "   (via Invoke-RestMethod, not a spawned process) to this machine's own"
Write-Output "   nova_api.py instance, which does the real git fetch/push/gh-pr-create work"
Write-Output "   (see nova_worktree_pr.py, 86bb3ceyf). Requires nova_api.py to actually be"
Write-Output "   running locally on the Aero to do anything useful."
$worktreePrKey = 'command="powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Nova\scripts\ssh_relay_worktree_pr.ps1",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP+kWsI6D65MZofTjyWaqFQz/smccZoF5+kSvaPSpSeb omen-to-aero-worktree-pr'
if ($worktreePrKey -match '<PASTE_PUBKEY_HERE>') {
    Write-Output "   SKIPPED -- placeholder pubkey not filled in yet. To enable:"
    Write-Output "     1. On the Omen: ssh-keygen -t ed25519 -N '' -f ~/.ssh/aero_keys/id_ed25519_aero_worktree_pr"
    Write-Output "     2. Copy its public half (cat ~/.ssh/aero_keys/id_ed25519_aero_worktree_pr.pub)"
    Write-Output "     3. Paste it into this script in place of <PASTE_PUBKEY_HERE>, then re-run"
    Write-Output "   Until then, POST /worktree-pr falls back to an honest SSH-transport error"
    Write-Output "   whenever it's served from the Omen."
} else {
    $content = Get-Content $keysFile
    if ($content -notcontains $worktreePrKey) {
        $content += $worktreePrKey
        Set-Content -Path $keysFile -Value $content -Encoding ascii
        icacls.exe $keysFile /inheritance:r | Out-Null
        icacls.exe $keysFile /grant "Administrators:F" | Out-Null
        icacls.exe $keysFile /grant "SYSTEM:F" | Out-Null
        Write-Output "   Installed."
    } else {
        Write-Output "   Already installed."
    }
}

Write-Output "10. Installing the sixth key (read-only, direct git -- diff)..."
Write-Output "    Opt-in, same shape as the worktree-pr key above, but this one runs git DIRECTLY"
Write-Output "    (ssh_read_aero_diff.ps1) rather than relaying through nova_api.py -- confirmed"
Write-Output "    live (86bb7pb6t follow-up) that a forced Omen-to-Aero SSH command CAN run"
Write-Output "    git.exe successfully (same mechanism the worktrees key already uses), so this"
Write-Output "    one doesn't need nova_api.py to be running locally on the Aero at all, unlike"
Write-Output "    the worktree-pr key. Backs nova_diff_link._remote_diff_from_aero() (86bb7pb6t)."
$diffKey = 'command="powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Nova\scripts\ssh_read_aero_diff.ps1",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFW+vj68VvRvxrc0sU1XEvbXBT4iOaa5G2GgcpZV7tL4 omen-to-aero-diff-readonly'
$content = Get-Content $keysFile
if ($content -notcontains $diffKey) {
    $content += $diffKey
    Set-Content -Path $keysFile -Value $content -Encoding ascii
    icacls.exe $keysFile /inheritance:r | Out-Null
    icacls.exe $keysFile /grant "Administrators:F" | Out-Null
    icacls.exe $keysFile /grant "SYSTEM:F" | Out-Null
    Write-Output "    Installed."
} else {
    Write-Output "    Already installed."
}

Write-Output "11. Restarting sshd to pick up all changes..."
Restart-Service sshd
Write-Output "    Done."

Write-Output ""
Write-Output "Setup complete. Verify from the Omen with:"
Write-Output '  ssh -i ~/.ssh/aero_keys/id_ed25519_aero_agentlog marvi@100.122.229.23 "ignored"'
Write-Output '  ssh -i ~/.ssh/aero_keys/id_ed25519_aero_worktrees marvi@100.122.229.23 "ignored"'
Write-Output '  ssh -i ~/.ssh/aero_keys/id_ed25519_aero_trainingdata marvi@100.122.229.23 "ignored"'
Write-Output '  ssh -i ~/.ssh/aero_keys/id_ed25519_aero_observabilitylogs marvi@100.122.229.23 "ignored"'
Write-Output '  echo {"kind":"blend_flag","index":0,"expected_timestamp":"...","correction":"test"} | ssh -i ~/.ssh/aero_keys/id_ed25519_aero_trainingflags_write marvi@100.122.229.23 "ignored"'
Write-Output '  echo {"branch":"nova-dispatch-00000000"} | ssh -i ~/.ssh/aero_keys/id_ed25519_aero_worktree_pr marvi@100.122.229.23 "ignored"'
Write-Output '  echo {"branch":"nova-dispatch-00000000"} | ssh -i ~/.ssh/aero_keys/id_ed25519_aero_diff marvi@100.122.229.23 "ignored"'
Write-Output "(the command argument is ignored either way -- every key always runs its forced script)"
