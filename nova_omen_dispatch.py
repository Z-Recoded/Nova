# nova_omen_dispatch.py
# Headless task dispatch on the Omen — the "invocation" step of 86bax0exx's
# orchestration layer (task-queue → headless run → review loop).
#
# Wraps `claude -p --worktree` over SSH, proven working live 2026-07-14 (see
# 86bax0exx's ClickUp comment). Worktree isolation uses Claude Code's own
# native --worktree flag, not nova_orchestrator.py's hand-rolled worktree
# logic — --worktree branches fresh from origin/master by default, so a
# dispatched task always runs against current pushed code regardless of the
# Omen's own local checkout state.
#
# Bounding mechanism, honestly: this CLI version has no --max-turns flag
# (checked directly, not assumed) — the real safety backstop here is a
# wall-clock subprocess timeout, not a turn count. 86bawx7vj's own "bounded
# headless runner" spec named --max-turns as one option; that option doesn't
# exist, so a timeout is what's actually enforced.
#
# This is the invocation primitive only. Not built: task-queue polling (a
# separate piece), escalation handling (86bax0wkj), pause-at-will (not
# built). Marvin picks the task and calls this directly for now, matching
# how nova_orchestrator.py's very first real task was manually kicked off
# before any queue existed.
#
# Never merges or deletes the worktree it creates — matches
# nova_orchestrator.py's own safety model exactly: a human reviews the diff
# and merges by hand.

import json
import shlex
import subprocess
from typing import Optional

OMEN_HOST = "100.114.197.117"  # Tailscale IP — works whether or not the Aero is on the same LAN as the Omen
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

DISPATCH_TIMEOUT_SECONDS = 1800  # 30 min hard ceiling — the real bounding mechanism, see module docstring


def dispatch_headless_task(task_description: str, worktree_name: Optional[str] = None) -> dict:
    """
    Dispatch one headless coding task to run via `claude -p --worktree` on
    the Omen, over SSH. Returns a dict with the real result: success,
    session_id (for resumability via `claude --resume`), the model's own
    summary text, cost, stop_reason, and turn count. On failure, returns
    success=False with an error message and the raw SSH/claude output for
    debugging — never raises.
    """
    worktree_flag = f"--worktree {worktree_name}" if worktree_name else "--worktree"
    quoted_task = shlex.quote(task_description)
    remote_command = (
        f"cd {OMEN_REPO_PATH} && "
        f"claude -p {worktree_flag} --permission-mode acceptEdits "
        f"--output-format json {quoted_task}"
    )

    try:
        ssh_result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", remote_command],
            capture_output=True,
            text=True,
            timeout=DISPATCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"Dispatch exceeded the {DISPATCH_TIMEOUT_SECONDS}s hard timeout",
        }

    if ssh_result.returncode != 0:
        return {
            "success": False,
            "error": f"SSH/claude exited {ssh_result.returncode}: {ssh_result.stderr.strip()}",
            "raw_stderr": ssh_result.stderr,
        }

    # claude -p's own stdout can carry warning lines before the JSON result
    # (e.g. the "workspace not trusted" notice seen in earlier live testing)
    # — find the actual result line rather than assuming stdout is pure JSON.
    json_line = None
    for line in ssh_result.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and '"type":"result"' in line:
            json_line = line
            break

    if json_line is None:
        return {
            "success": False,
            "error": "No result JSON found in claude's output",
            "raw_stdout": ssh_result.stdout,
        }

    result = json.loads(json_line)
    return {
        "success": not result.get("is_error", False) and result.get("stop_reason") == "end_turn",
        "session_id": result.get("session_id"),
        "summary": result.get("result"),
        "stop_reason": result.get("stop_reason"),
        "cost_usd": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
    }


if __name__ == "__main__":
    import sys

    task = sys.argv[1] if len(sys.argv) > 1 else "Reply with exactly: dispatch module smoke test ok"
    print(json.dumps(dispatch_headless_task(task), indent=2))
