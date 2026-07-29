# nova_headless_approval_hook.py
# Claude Code `PreToolUse` hook (matcher: Bash) closing the headless-lane
# gap left by 86bb3ceym (filed as 86bb3r0h4). 86bb3ceym's pre-action
# approval gate only covers the Aero interactive lane
# (nova_orchestrator.py's hand-rolled tool loop, driven by the raw
# Anthropic API) -- the Omen headless dispatch lane runs the real `claude`
# CLI directly over SSH and never touches that code path at all. This
# script is that lane's real equivalent: a genuine Claude Code
# `PreToolUse` hook, wired into .claude/settings.json (a tracked file, so
# it ships into every dispatch worktree automatically via
# `git worktree add ... origin/master`), doing the same
# register -> notify -> poll -> decide flow as
# nova_orchestrator._request_tool_approval(), just from a standalone
# subprocess instead of an in-process function call.
#
# Verified during design (via Claude Code's own hooks docs) that
# PreToolUse hooks are enforced independently of permission-mode -- a
# "deny" here still blocks the call under both --permission-mode
# acceptEdits (dispatch_headless_task(), resume_headless_task()) and
# --permission-mode bypassPermissions (dispatch_headless_task_sandboxed()).
#
# Scoping: only acts when NOVA_HEADLESS_DISPATCH=1 is set in the
# environment -- nova_omen_dispatch.py sets this on every real headless
# invocation. Without it, this script is a silent no-op, so it never
# gates Marvin's own interactive Claude Code sessions in this repo.
#
# Registers against the Omen's own always-on nova-api over its Tailscale
# IP (not 127.0.0.1) -- the sandboxed dispatch path runs this hook inside
# a Docker container with no --network host, so loopback wouldn't reach
# the host's nova-api, but the Tailscale IP does from both bare-SSH and
# sandboxed contexts alike (same NOVA_API_URL default as
# nova_escalation.py, for the same reason).

import json
import os
import sys
import time
import urllib.error
import urllib.request

from nova_config import (
    get_approval_gate_patterns,
    get_approval_gate_poll_interval_seconds,
    get_approval_gate_timeout_seconds,
    is_pre_action_approval_gate_enabled,
)
from nova_notify import send_notification

NOVA_API_URL = os.environ.get("NOVA_API_URL", "http://100.114.197.117:8001")


def _allow() -> None:
    # No stdout output at all is a valid "no opinion" PreToolUse response --
    # matches this same settings.json's existing graphify-context hooks'
    # own convention of only printing JSON when they have something to say.
    sys.exit(0)


def _emit_decision(decision: str, reason: str | None = None) -> None:
    """
    Print the hookSpecificOutput JSON Claude Code expects from a PreToolUse
    hook and exit 0 -- the JSON-output method requires exit 0 regardless of
    decision; exit code 2 is a separate, mutually exclusive block mechanism
    this script doesn't use.
    """
    hook_output = {"hookEventName": "PreToolUse", "permissionDecision": decision}
    if reason:
        hook_output["permissionDecisionReason"] = reason
    print(json.dumps({"hookSpecificOutput": hook_output}))
    sys.exit(0)


def _matched_pattern(command: str) -> str | None:
    """Same case-insensitive substring check as nova_orchestrator._approval_gate_reason()."""
    command_lower = command.lower()
    for pattern in get_approval_gate_patterns():
        if pattern.lower() in command_lower:
            return pattern
    return None


def _register_approval(
    tool_name: str, tool_input: dict, reason: str, session_id: str | None, cwd: str | None
) -> str | None:
    """POST /tool-approvals (create) — returns the new approval_id, or None on any failure."""
    payload = json.dumps(
        {
            "tool_name": tool_name,
            "tool_input": tool_input,
            "reason": reason,
            "session_id": session_id,
            "cwd": cwd,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{NOVA_API_URL}/tool-approvals",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        # Fixed internal API URL, never user-controlled -- not the file:/custom-scheme risk this check targets.
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
            record = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None
    return record.get("approval_id")


def _poll_decision(approval_id: str) -> dict | None:
    """Sleep-poll GET /tool-approvals until this approval_id leaves "pending", or the configured timeout elapses."""
    timeout_seconds = get_approval_gate_timeout_seconds()
    poll_interval = get_approval_gate_poll_interval_seconds()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            # Fixed internal API URL, never user-controlled -- not the file:/custom-scheme risk this check targets.
            with urllib.request.urlopen(f"{NOVA_API_URL}/tool-approvals", timeout=10) as response:  # nosec B310
                pending = json.loads(response.read())
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
            continue
        record = pending.get(approval_id)
        if record and record.get("status") != "pending":
            return record
    return None


def _report_timeout(approval_id: str) -> None:
    """
    Best-effort self-report to POST /tool-approvals/{id}/timeout so the
    Controller doesn't show this record stuck at "pending" forever. Never
    raises -- the deny decision below is what actually matters for the
    running dispatch; this is UI hygiene only.
    """
    request = urllib.request.Request(f"{NOVA_API_URL}/tool-approvals/{approval_id}/timeout", method="POST")
    try:
        # Fixed internal API URL, never user-controlled -- not the file:/custom-scheme risk this check targets.
        urllib.request.urlopen(request, timeout=10)  # nosec B310
    except (urllib.error.URLError, urllib.error.HTTPError):
        pass


def main() -> None:
    if not os.environ.get("NOVA_HEADLESS_DISPATCH"):
        _allow()

    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        _allow()

    if hook_input.get("tool_name") != "Bash":
        _allow()

    if not is_pre_action_approval_gate_enabled():
        _allow()

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")
    pattern = _matched_pattern(command)
    if not pattern:
        _allow()

    reason = f"matched approval-gate pattern '{pattern}'"
    session_id = hook_input.get("session_id")
    cwd = hook_input.get("cwd")

    approval_id = _register_approval("Bash", tool_input, reason, session_id, cwd)
    if not approval_id:
        # Registration itself failed (nova-api unreachable) -- fail closed,
        # same "don't assume the safe case" instinct as
        # nova_escalation.is_dispatch_paused().
        _emit_decision("deny", f"Could not register approval request ({reason}) — nova-api unreachable.")

    send_notification(
        title="Nova: headless approval needed",
        message=f"Bash: {reason}",
        tags="warning",
        priority="high",
    )

    decision_record = _poll_decision(approval_id)
    if decision_record is None:
        _report_timeout(approval_id)
        _emit_decision("deny", f"Approval timed out ({reason}).")

    if decision_record.get("status") == "approved":
        _emit_decision("allow")
    _emit_decision("deny", f"Denied by approver ({reason}).")


if __name__ == "__main__":
    main()
