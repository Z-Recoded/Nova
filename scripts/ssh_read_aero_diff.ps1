# ssh_read_aero_diff.ps1
# Forced command for the Omen-to-Aero "diff" read-only SSH key
# (C:\ProgramData\ssh\administrators_authorized_keys, command= restriction).
# The sixth key in this bridge. Invoked by
# nova_diff_link._remote_diff_from_aero() when nova_api.py is running on
# the Omen and needs a real diff for a branch that only exists on the Aero
# (86bb7pb6t follow-up).
#
# Runs git DIRECTLY -- unlike ssh_relay_worktree_pr.ps1's HTTP-relay
# workaround, confirmed live this session that a forced Omen-to-Aero SSH
# command running git.exe works fine (same mechanism
# ssh_read_worktrees.ps1 already uses successfully). The "CreateProcess
# blocked for this class of SSH session" finding in
# ssh_relay_worktree_pr.ps1's own docstring, made for python.exe, didn't
# generalize to git.exe the way it assumed.
#
# A forced command's command= restriction ignores whatever the SSH
# client's own command string was, so -- same as ssh_relay_worktree_pr.ps1
# -- the real input (the branch name) is read from stdin as JSON, not from
# the SSH command line.
#
# Real, independent trust boundary: this script validates the branch name
# itself, matching nova_diff_link._validate_branch()'s exact structural
# rule, rather than trusting that the caller already checked -- whatever
# reaches this key over SSH is untrusted until this script says otherwise.

Set-Location "C:\Nova"

$stdin = [Console]::OpenStandardInput()
$inputStream = New-Object System.IO.MemoryStream
$stdin.CopyTo($inputStream)
$requestText = [System.Text.Encoding]::UTF8.GetString($inputStream.ToArray())
$requestText = $requestText.TrimStart([char]0xFEFF)

function Write-JsonResponse {
    param($ResponseObject)
    $json = $ResponseObject | ConvertTo-Json -Compress -Depth 10
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $stdout = [Console]::OpenStandardOutput()
    $stdout.Write($bytes, 0, $bytes.Length)
    $stdout.Flush()
}

try {
    $req = $requestText | ConvertFrom-Json -ErrorAction Stop
} catch {
    Write-JsonResponse @{ exists = $false; diff_text = $null; error = "Malformed request: $($_.Exception.Message)" }
    exit
}

$branch = $req.branch

# Same structural rule as nova_diff_link.NOVA_AGENT_BRANCH_PATTERN /
# DISPATCH_BRANCH_PATTERN -- kept in sync by hand (this is PowerShell, not
# something that can import the Python module).
$isNovaAgent = $branch -cmatch '^nova-agent/[a-z0-9][a-z0-9-]*$'
$isNovaDispatch = $branch -cmatch '^nova-dispatch-[0-9a-f]{8}$'
if (-not ($isNovaAgent -or $isNovaDispatch)) {
    Write-JsonResponse @{ exists = $false; diff_text = $null; error = "'$branch' doesn't look like a real Nova branch" }
    exit
}

git rev-parse --verify "refs/heads/$branch" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-JsonResponse @{ exists = $false; diff_text = $null; error = $null }
    exit
}

$diffOutput = git diff "master...$branch" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-JsonResponse @{ exists = $true; diff_text = $null; error = ($diffOutput -join "`n") }
} else {
    Write-JsonResponse @{ exists = $true; diff_text = ($diffOutput -join "`n"); error = $null }
}
