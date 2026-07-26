# fix_sshd_listenaddress.ps1
# One-time repair for the real bug found live 2026-07-25 while setting up
# 86bb3cey2/86bb3ceyc's Omen-to-Aero SSH access: the original
# setup_omen_to_aero_ssh.ps1 appended "ListenAddress <tailscale-ip>" via a
# blind Add-Content, landing it AFTER Windows' default
# "Match Group administrators" block. ListenAddress isn't in the small set
# of directives OpenSSH allows inside a Match block, so sshd refused to
# parse the config at all and failed to start. setup_omen_to_aero_ssh.ps1
# itself is already fixed for future/fresh runs -- this script repairs an
# sshd_config that's already in the broken state from the earlier run.
# Run once, elevated. Safe to re-run (no-ops if already fixed).

$ErrorActionPreference = "Stop"
$path = "C:\ProgramData\ssh\sshd_config"
$listenLine = "ListenAddress 100.122.229.23"

$lines = Get-Content $path
$lines = $lines | Where-Object { $_ -ne $listenLine }

$matchLineNumber = ($lines | Select-String -Pattern '^\s*Match\s' | Select-Object -First 1).LineNumber
if (-not $matchLineNumber) {
    Write-Output "No Match block found -- appending normally."
    $newLines = $lines + @($listenLine)
} else {
    $before = $lines[0..($matchLineNumber - 2)]
    $after = $lines[($matchLineNumber - 1)..($lines.Count - 1)]
    $newLines = $before + @($listenLine, "") + $after
}

Set-Content -Path $path -Value $newLines -Encoding ascii

Write-Output "Fixed. Config tail now:"
Get-Content $path | Select-String -Pattern "ListenAddress|Match" -Context 1, 1

Write-Output ""
Write-Output "Starting sshd..."
Start-Service sshd
Get-Service sshd
