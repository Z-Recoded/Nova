# ssh_relay_worktree_pr.ps1
# Forced command for the Omen-to-Aero "worktree-pr" SSH key
# (C:\ProgramData\ssh\administrators_authorized_keys, command= restriction).
# The fifth key in this bridge. Invoked by
# nova_worktree_pr._dispatch_create_pr_to_aero() when a Controller
# "Create PR" tap is served from the Omen (86bb3ceyf).
#
# Deliberately does NOT run git or gh itself. Confirmed during the
# abort/kill-switch build (86bb3ceyj) that SSH public-key sessions on
# Windows use a "network logon" token Windows refuses to use for
# CreateProcess at all -- the failure for python.exe was a generic Win32
# "Access is denied" at the CreateProcess level, not anything specific to
# that one binary, so it applies equally to git.exe/gh.exe. Instead, this
# relays the request via Invoke-RestMethod (a native .NET HTTP call, NOT
# a spawned process -- confirmed unaffected by the same restriction) to
# the Aero's OWN already-running nova_api.py instance, which -- being a
# normal locally-launched process, not an SSH session -- has no such
# restriction and does the real git fetch/push/gh-pr-create work
# directly (nova_worktree_pr._create_pr_locally()).
#
# Real, honest precondition: this only works if nova_api.py is actually
# running locally on the Aero right now (Task Scheduler's "Nova
# Auto-Start" launches it at login, so this holds whenever Marvin's
# logged in, but it's a real dependency, not guaranteed). If it's not
# running, Invoke-RestMethod's own connection-refused error surfaces
# honestly below rather than silently doing nothing.
#
# Raw-byte passthrough for the request read -- same encoding discipline
# as every other forced script here.

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
    Write-JsonResponse @{ success = $false; error = "Malformed relay request: $($_.Exception.Message)" }
    exit
}

# The escalation token lives in this machine's own .env -- read directly
# here rather than trusting anything the caller supplies. The whole point
# of the token gate on /worktree-pr is that a caller can't just assert
# authorization; reading it locally (not passed over the wire from the
# Omen) keeps that guarantee intact for this relay hop too.
$token = $null
$envPath = "C:\Nova\.env"
if (Test-Path $envPath) {
    foreach ($line in Get-Content $envPath) {
        if ($line -match '^NOVA_ESCALATION_TOKEN=(.*)$') { $token = $matches[1].Trim() }
    }
}

try {
    $body = @{ branch = $req.branch } | ConvertTo-Json -Compress
    $response = Invoke-RestMethod -Uri "http://localhost:8000/worktree-pr" -Method Post `
        -Headers @{ "X-Nova-Escalation-Token" = $token } -ContentType "application/json" -Body $body -TimeoutSec 40
    Write-JsonResponse $response
} catch {
    # Invoke-RestMethod on PS 5.1 puts a non-2xx response's real JSON body
    # in $_.ErrorDetails.Message -- surface that if it parses, so a real
    # business-logic failure (e.g. "git push failed: ...") reaches the
    # Omen intact instead of being flattened into a generic .NET exception
    # message.
    $errorBody = $null
    if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
        try { $errorBody = $_.ErrorDetails.Message | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    if ($errorBody) {
        Write-JsonResponse $errorBody
    } else {
        # A plain connection failure (nova_api.py not actually running on
        # the Aero -- the most likely real-world case, and confirmed live:
        # PS 5.1 wraps this specific failure in an unhelpful generic
        # "Object reference not set to an instance of an object" message
        # rather than a clear connection-refused one) gets a clearer,
        # actionable message instead of passing that through as-is.
        $rawMessage = $_.Exception.Message
        $detail = if ($rawMessage -match 'Object reference not set|Unable to connect|actively refused') {
            "Could not reach nova_api.py on http://localhost:8000 -- it may not be running on the Aero right now."
        } else {
            "Relay to local nova_api.py failed: $rawMessage"
        }
        Write-JsonResponse @{ success = $false; error = $detail }
    }
}
