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
# is stdin, piped straight into nova_patch_training_flags_cli.py, which
# never accepts a file path from its caller and only ever touches
# C:\Nova\logs\training_flags.jsonl, one specific field (correction or
# verification_status) at a time, gated by the same index/timestamp
# safety check the local decide route has always used
# (nova_training_flags_patch.patch_training_flags_entry()).
#
# Raw-byte passthrough both directions -- same encoding fix already needed
# for the read scripts (ssh_read_agent_log.ps1): PowerShell's default text
# pipeline re-encodes through the console's active codepage, which would
# corrupt non-ASCII correction text (or the JSON response) silently
# otherwise.
#
# CreateNoWindow, found live during real verification (2026-07-26): sshd
# runs as a Windows service with no interactive desktop session. Without
# CreateNoWindow set, starting a console-subsystem process (python.exe)
# makes .NET try to allocate a console window on the interactive window
# station -- which the service session has no access to -- and Process.Start
# fails outright with "Access is denied," not a Python-level error. The
# three read-only scripts never hit this because none of them spawn a
# process. RedirectStandardError added at the same time so a real Python
# exception surfaces in the response instead of silently vanishing.

$stdin = [Console]::OpenStandardInput()
$inputStream = New-Object System.IO.MemoryStream
$stdin.CopyTo($inputStream)
$inputBytes = $inputStream.ToArray()

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "C:\Nova\nova-env\Scripts\python.exe"
$psi.Arguments = "C:\Nova\nova_patch_training_flags_cli.py"
$psi.WorkingDirectory = "C:\Nova"
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true

$proc = [System.Diagnostics.Process]::Start($psi)
$proc.StandardInput.BaseStream.Write($inputBytes, 0, $inputBytes.Length)
$proc.StandardInput.BaseStream.Close()

$outputStream = New-Object System.IO.MemoryStream
$proc.StandardOutput.BaseStream.CopyTo($outputStream)
$stderrText = $proc.StandardError.ReadToEnd()
$proc.WaitForExit()
$outputBytes = $outputStream.ToArray()

# If the CLI produced no stdout at all, something crashed before it could
# write a JSON response (e.g. a Python import error) -- surface stderr as a
# JSON envelope instead of returning nothing, so the caller sees a real
# error rather than an empty/malformed response.
if ($outputBytes.Length -eq 0 -and $stderrText) {
    $errorJson = (@{ ok = $false; status = 500; detail = "Aero-side CLI crashed: $stderrText" } | ConvertTo-Json -Compress)
    $outputBytes = [System.Text.Encoding]::UTF8.GetBytes($errorJson)
}

$stdout = [Console]::OpenStandardOutput()
$stdout.Write($outputBytes, 0, $outputBytes.Length)
$stdout.Flush()
