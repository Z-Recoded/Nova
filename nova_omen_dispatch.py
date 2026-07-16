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
# separate piece), real escalation *detection* logic (86bax0wkj — the
# check_escalation() hook below is a stub only). Pause-at-will and the
# escalation-hook interface itself are built (nova_escalation.py, 2026-07-
# 14). Marvin picks the task and calls this directly for now, matching how
# nova_orchestrator.py's very first real task was manually kicked off
# before any queue existed.
#
# Never merges or deletes the worktree it creates — matches
# nova_orchestrator.py's own safety model exactly: a human reviews the diff
# and merges by hand.
#
# Dual-fuel credential switch (2026-07-16, 86bawpvzz groundwork): dispatch
# now picks which credential `claude -p` uses per run instead of always
# taking whatever the shell happens to expose. Default is the Omen's own
# Claude Code subscription login (confirmed live via `claude auth status`
# — Pro plan, no ANTHROPIC_API_KEY in the shell env today), which draws
# from Marvin's otherwise-idle usage capacity for free. Falls back to the
# Omen's existing funded metered ANTHROPIC_API_KEY (confirmed present in
# .env, confirmed funded) whenever the current hour isn't confirmed idle
# against the real Claude Code activity profile (nova_usage_logger.py /
# nova_api.py's /activity-profile). Three decisions confirmed directly with
# Marvin rather than assumed: hardcoded America/Chicago timezone (matches
# this machine's observed UTC offset), an hour only counts as idle if it
# has shown exactly zero messages in the 60-day window (the strictest
# option offered), and any missing/ambiguous signal fails toward the
# metered key, never toward assumed-idle. See choose_fuel_source().

import argparse
import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from nova_escalation import check_escalation, is_dispatch_paused, set_dispatch_pause

OMEN_HOST = "100.114.197.117"  # Tailscale IP — works whether or not the Aero is on the same LAN as the Omen
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"
OMEN_ENV_PATH = f"{OMEN_REPO_PATH}/.env"
# Confirmed live: the Omen's own venv has python-dotenv installed; bare
# `python3` on the Omen does not.
OMEN_VENV_PYTHON = f"{OMEN_REPO_PATH}/nova-env/bin/python"

DISPATCH_TIMEOUT_SECONDS = 1800  # 30 min hard ceiling — the real bounding mechanism, see module docstring

# Where the merged, cross-machine Claude Code activity profile lives — the
# Omen's own nova-api, matching nova_usage_logger.py's push target.
NOVA_API_URL = os.environ.get("NOVA_API_URL", f"http://{OMEN_HOST}:8001")

# Hardcoded rather than read from the OS clock's configured zone — Marvin
# confirmed this directly rather than letting it be an assumption.
LOCAL_TIMEZONE = ZoneInfo("America/Chicago")

# An hour only counts as idle if it has shown exactly zero messages across
# the activity profile's 60-day window — the strictest of the options
# Marvin was offered, chosen explicitly.
IDLE_THRESHOLD_MESSAGES = 0

# Only this machine's activity profile reflects Marvin's real interactive
# use. The Omen's own dispatched-task sessions would land under a
# different source-machine key (e.g. "nova") if ever logged there, and
# must never be treated as a human-activity signal.
INTERACTIVE_SOURCE_MACHINE = "zeed"


def _get_activity_count(now: datetime) -> Optional[int]:
    """
    Look up INTERACTIVE_SOURCE_MACHINE's message count for `now`'s local
    weekday/hour from the Omen's merged activity profile (GET
    /activity-profile). Returns None — never 0, never raises — on any
    failure: unreachable API, missing machine key, or an unexpected shape.
    Callers must be able to tell "confirmed zero activity" apart from
    "couldn't tell," since those two cases get treated oppositely.
    """
    try:
        with urllib.request.urlopen(f"{NOVA_API_URL}/activity-profile", timeout=10) as response:
            profile = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None

    machine_profile = profile.get(INTERACTIVE_SOURCE_MACHINE)
    if not machine_profile:
        return None

    local_now = now.astimezone(LOCAL_TIMEZONE)
    try:
        # weekday 0 = Monday, matching both nova_usage_logger.py's own
        # convention and datetime.weekday() — a mismatch here would
        # silently corrupt every lookup without raising anything.
        return machine_profile["counts"][local_now.weekday()][local_now.hour]
    except (KeyError, IndexError, TypeError):
        return None


def choose_fuel_source(now: Optional[datetime] = None) -> str:
    """
    Decide which credential this dispatch should use: "subscription" (the
    default — free, draws from Marvin's own otherwise-idle Claude Code
    capacity) during a confirmed-idle hour, or "api_key" (the funded
    metered fallback) otherwise. This is a statistical prior from
    historical activity, not a live occupancy check — it reduces the odds
    of silently competing with Marvin's interactive usage, it doesn't
    eliminate them. Fails toward "api_key" whenever the signal is missing
    or ambiguous, per Marvin's explicit instruction — never assumes idle.
    """
    now = now or datetime.now(tz=LOCAL_TIMEZONE)
    activity_count = _get_activity_count(now)
    if activity_count is None:
        return "api_key"
    return "subscription" if activity_count <= IDLE_THRESHOLD_MESSAGES else "api_key"


def _build_credential_prefix(fuel_source: str) -> str:
    """
    Build the shell prefix that controls which credential `claude` sees
    for one dispatch invocation. Must be placed immediately in front of
    the `claude` command itself, never wrapping `cd` (a shell builtin, not
    something `env` can exec).

    "subscription": explicitly unset both credential env vars right before
    exec, regardless of what the shell already carries — env -u strips at
    exec() time, not shell-startup time, so this holds even against a
    future stray export in a shell init file, not just today's confirmed-
    clean state.

    "api_key": extract only ANTHROPIC_API_KEY from .env via the Omen's own
    venv + python-dotenv (confirmed installed there), rather than sourcing
    the whole file. Confirmed live: no .mcp.json in this repo registers
    nova_tools.py's restricted-env wrapper for this path — headless
    `claude -p` uses Claude Code's own native Bash tool, which inherits the
    full real OS environment. A blanket `source .env` would leak
    CLICKUP_API_KEY/RUNPOD_API_KEY into every tool call the headless
    session makes, for no reason.
    """
    if fuel_source == "subscription":
        return "env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN"
    dotenv_code = (
        f"from dotenv import dotenv_values; "
        f"print(dotenv_values('{OMEN_ENV_PATH}').get('ANTHROPIC_API_KEY', ''))"
    )
    return f'ANTHROPIC_API_KEY=$({OMEN_VENV_PYTHON} -c "{dotenv_code}")'


def dispatch_headless_task(
    task_description: str,
    worktree_name: Optional[str] = None,
    fuel_source: str = "auto",
) -> dict:
    """
    Dispatch one headless coding task to run via `claude -p --worktree` on
    the Omen, over SSH. Returns a dict with the real result: success,
    fuel_source (which credential actually ran — "subscription" or
    "api_key", attached to every return path, not just the success one),
    session_id (for resumability via `claude --resume`), the model's own
    summary text, cost, stop_reason, and turn count. On failure, returns
    success=False with an error message and the raw SSH/claude output for
    debugging — never raises.

    fuel_source: "auto" (default) resolves via choose_fuel_source() against
    the real activity profile; pass "subscription" or "api_key" directly to
    force one, bypassing the idle-window check entirely.
    """
    resolved_fuel_source = choose_fuel_source() if fuel_source == "auto" else fuel_source

    pause_state = is_dispatch_paused()
    if pause_state["paused"]:
        return {
            "success": False,
            "paused": True,
            "fuel_source": resolved_fuel_source,
            "reason": pause_state.get("reason"),
            "error": "Dispatch is paused — call set_dispatch_pause(False) or "
                     "`python nova_omen_dispatch.py --resume` to clear it.",
        }

    worktree_flag = f"--worktree {worktree_name}" if worktree_name else "--worktree"
    quoted_task = shlex.quote(task_description)
    credential_prefix = _build_credential_prefix(resolved_fuel_source)
    remote_command = (
        f"cd {OMEN_REPO_PATH} && {credential_prefix} "
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
            "fuel_source": resolved_fuel_source,
            "error": f"Dispatch exceeded the {DISPATCH_TIMEOUT_SECONDS}s hard timeout",
        }

    if ssh_result.returncode != 0:
        return {
            "success": False,
            "fuel_source": resolved_fuel_source,
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
            "fuel_source": resolved_fuel_source,
            "error": "No result JSON found in claude's output",
            "raw_stdout": ssh_result.stdout,
        }

    result = json.loads(json_line)
    dispatch_result = {
        "success": not result.get("is_error", False) and result.get("stop_reason") == "end_turn",
        "fuel_source": resolved_fuel_source,
        "session_id": result.get("session_id"),
        "summary": result.get("result"),
        "stop_reason": result.get("stop_reason"),
        "cost_usd": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
    }
    dispatch_result["escalation"] = check_escalation(dispatch_result)
    return dispatch_result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dispatch a headless coding task to the Omen.")
    parser.add_argument("task", nargs="?", help="Task description. Omitted with --pause/--resume.")
    parser.add_argument("--pause", metavar="REASON", help="Pause dispatch until --resume is called.")
    parser.add_argument("--resume", action="store_true", help="Clear a previously set pause.")
    parser.add_argument(
        "--fuel-source",
        choices=["auto", "subscription", "api_key"],
        default="auto",
        help="Which credential to use ('auto' checks the real activity profile; "
             "default: auto).",
    )
    args = parser.parse_args()

    if args.pause is not None:
        print(json.dumps(set_dispatch_pause(True, args.pause), indent=2))
    elif args.resume:
        print(json.dumps(set_dispatch_pause(False), indent=2))
    else:
        task = args.task or "Reply with exactly: dispatch module smoke test ok"
        print(json.dumps(dispatch_headless_task(task, fuel_source=args.fuel_source), indent=2))
