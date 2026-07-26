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
# This is the invocation primitive. Task-queue polling is a separate piece
# (nova_task_queue.py/nova_scheduled_dispatch.py). Real escalation
# detection (86bax0wkj) is now built: every dispatch always creates an
# explicitly-named worktree (never the bare --worktree flag) and captures
# its real path via a before/after `git worktree list --porcelain` diff,
# since claude -p's own JSON result never reports it; resume_headless_task()
# uses that captured path to `claude -p --resume <session_id>` back into
# the exact same worktree once Marvin answers via /escalations-ui, with no
# --worktree flag at all (that would create a new worktree instead of
# continuing this one).
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
import base64
import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import datetime
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

# Abort/kill switch targets (86bb3ceyj) -- fixed, not per-task, names/paths.
# Safe because nova_scheduled_dispatch.py's lock already guarantees at most
# one cron-fired dispatch runs at a time, so there's never a collision to
# disambiguate. REMOTE_DISPATCH_PID_PATH is where the bare-SSH path's
# remote_command writes the REAL claude -p PID the moment it's spawned (see
# dispatch_headless_task() below) -- necessary because _run_claude_over_ssh()
# allocates no pty, so killing the wrapper process or its SSH client does
# NOT reliably kill the remote command; it just orphans it, still running.
# SANDBOXED_CONTAINER_NAME is the fixed --name given to the sandboxed path's
# Docker container so it can be `docker kill`ed directly, without needing
# any PID-file trick.
REMOTE_DISPATCH_PID_PATH = f"{OMEN_REPO_PATH}/logs/current_dispatch_remote_pid"
SANDBOXED_CONTAINER_NAME = "nova-dispatch-current"

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

# Runs on the OMEN (via SSH, right after a dispatch) to convert that
# session's own Claude Code transcript into agent_log.jsonl-shaped turn
# entries. Exists because headless `claude -p` sessions never went through
# nova_orchestrator.py's in-process turn loop, so none of this data was
# reaching the training corpus a future Qwen3 8B fine-tune needs (found
# 2026-07-16 while checking real progress toward Phase 3.5's swap trigger —
# the whole point of proving this dispatch mechanism was to grow that
# corpus, and it wasn't wired in at all). Session transcripts under
# ~/.claude/projects/**/<session_id>.jsonl already carry everything
# _log_agent_turn() needs — message.model, .stop_reason, .usage, tool_use
# content blocks — plus gitBranch/slug per line, so no reconstruction is
# needed, just extraction. Runs as a standalone script (not importing this
# repo's own modules) since it executes on the remote Python, which may not
# have this repo's dependencies installed for a bare `python3` call.
#
# Idempotent as of 86bax0wkj (2026-07-18): a resumed session's transcript
# is the same, now-longer, file as the original — re-running this against
# it must only append the genuinely-new post-resume turns, not re-append
# everything from the start. Fixed via a small per-session cursor file,
# logs/agent_log_ingest_cursor.json ({session_id: last_ingested_turn}),
# read/written inside this remote script since the cursor only needs to
# exist where the transcript and agent_log.jsonl already live. Turn
# numbering itself is unchanged (still counts every assistant-type
# transcript line from the start, so numbers stay stable across calls) —
# only which entries get appended changes.
_AGENT_LOG_CONVERTER_SCRIPT = """
import glob, json, os, sys, base64

session_id = sys.argv[1]
task_description = base64.b64decode(sys.argv[2]).decode("utf-8")
repo_path = os.path.expanduser("~/nova")
projects_glob = os.path.join(os.path.expanduser("~/.claude/projects"), "**", session_id + ".jsonl")
matches = glob.glob(projects_glob, recursive=True)
if not matches:
    print(json.dumps({"converted": 0, "error": "transcript not found"}))
    sys.exit(0)

cursor_path = os.path.join(repo_path, "logs", "agent_log_ingest_cursor.json")
try:
    with open(cursor_path, "r", encoding="utf-8") as f:
        cursor = json.load(f)
except (FileNotFoundError, json.JSONDecodeError):
    cursor = {}
already_ingested = cursor.get(session_id, 0)

entries = []
turn = 0
branch = None
slug = None
with open(matches[0], "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "assistant":
            continue
        message = d.get("message") or {}
        usage = message.get("usage") or {}
        turn += 1
        branch = d.get("gitBranch") or branch
        slug = d.get("slug") or slug
        if turn <= already_ingested:
            continue
        tool_calls = [
            {"name": block.get("name"), "input": block.get("input")}
            for block in (message.get("content") or [])
            if block.get("type") == "tool_use"
        ]
        entries.append({
            "timestamp": d.get("timestamp"),
            "task_slug": slug,
            "branch": branch,
            "turn": turn,
            "task": task_description,
            "skill_category": None,
            "skill_version": None,
            "stop_reason": message.get("stop_reason"),
            "tool_calls": tool_calls,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
            "model": message.get("model"),
            "source": "headless_dispatch",
            "session_id": session_id,
        })

os.makedirs(os.path.join(repo_path, "logs"), exist_ok=True)
with open(os.path.join(repo_path, "logs", "agent_log.jsonl"), "a", encoding="utf-8") as f:
    for e in entries:
        f.write(json.dumps(e, ensure_ascii=False) + "\\n")

cursor[session_id] = turn
with open(cursor_path, "w", encoding="utf-8") as f:
    json.dump(cursor, f)

print(json.dumps({"converted": len(entries), "branch": branch, "transcript_path": matches[0]}))
"""


def _ingest_transcript_into_agent_log(session_id: str, task_description: str) -> dict:
    """
    Convert one headless dispatch's Claude Code transcript into
    agent_log.jsonl-shaped turn entries and append them to the Omen's own
    logs/agent_log.jsonl — via SSH, since the transcript only exists on the
    Omen regardless of which machine dispatch_headless_task() was called
    from. Never raises: a failure here must not take down a dispatch that
    already succeeded at its real job. Returns {"converted": N} on success,
    or {"converted": 0, "error": "..."} if the transcript couldn't be found
    or converted.

    Verified live against a real dispatch transcript (2026-07-16, the
    86baux7bb Chonkie eval): 36/36 turns converted correctly, tool_calls and
    token counts matched the raw transcript exactly. One known caveat found
    during that verification: the "branch" field comes from the transcript's
    own gitBranch metadata, which reported "master" for a --worktree session
    rather than the worktree's real branch — task_slug (from the
    transcript's own "slug" field) is the reliable per-session identifier,
    not branch.

    Idempotent as of 86bax0wkj: safe to call again against the same
    session_id after a resume appends new turns to the same transcript —
    only genuinely-new turns get appended to agent_log.jsonl, via a cursor
    file the remote script itself maintains (see the script's own comment
    above). One accepted, honest gap: resume_headless_task() passes the
    answer text as task_description here (it doesn't have the original
    task string in scope), so post-resume turns' "task" field reads as the
    answer, not the original prompt — task_slug still ties every turn
    (pre- and post-resume) back to the same session correctly.
    """
    encoded_script = base64.b64encode(_AGENT_LOG_CONVERTER_SCRIPT.encode("utf-8")).decode("ascii")
    encoded_task = base64.b64encode(task_description.encode("utf-8")).decode("ascii")
    remote_command = (
        f"python3 -c {shlex.quote('import base64; exec(base64.b64decode(' + repr(encoded_script) + ').decode())')} "
        f"{shlex.quote(session_id)} {shlex.quote(encoded_task)}"
    )
    try:
        result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", remote_command],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return {"converted": 0, "error": f"converter exited {result.returncode}: {result.stderr.strip()}"}
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError) as e:
        return {"converted": 0, "error": f"{type(e).__name__}: {e}"}


def _get_activity_count(now: datetime) -> int | None:
    """
    Look up INTERACTIVE_SOURCE_MACHINE's message count for `now`'s local
    weekday/hour from the Omen's merged activity profile (GET
    /activity-profile). Returns None — never 0, never raises — on any
    failure: unreachable API, missing machine key, or an unexpected shape.
    Callers must be able to tell "confirmed zero activity" apart from
    "couldn't tell," since those two cases get treated oppositely.
    """
    try:
        # Fixed internal API URL, never user-controlled -- not the file:/custom-scheme risk this check targets.
        with urllib.request.urlopen(  # nosec B310
            f"{NOVA_API_URL}/activity-profile", timeout=10
        ) as response:
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


def choose_fuel_source(now: datetime | None = None) -> str:
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
        f"from dotenv import dotenv_values; print(dotenv_values('{OMEN_ENV_PATH}').get('ANTHROPIC_API_KEY', ''))"
    )
    return f'ANTHROPIC_API_KEY=$({OMEN_VENV_PYTHON} -c "{dotenv_code}")'


def _run_claude_over_ssh(remote_command: str, timeout: int = DISPATCH_TIMEOUT_SECONDS) -> dict:
    """
    Shared SSH-run-and-parse logic for both a fresh dispatch and a resume
    (86bax0wkj) — extracted so resume_headless_task() doesn't duplicate it.
    Runs remote_command over SSH, scans stdout for claude -p's own
    "type":"result" JSON line (claude -p's stdout can carry warning lines
    before it, e.g. the "workspace not trusted" notice seen in earlier live
    testing — find the real result line rather than assuming stdout is
    pure JSON), and decodes it.

    Returns the parsed result dict (claude -p's own JSON shape) on success,
    or {"error": ..., "raw_stdout"/"raw_stderr": ...} on any failure
    (timeout, non-zero exit, no result JSON found) — never raises. Callers
    branch on `"error" in raw`.
    """
    try:
        ssh_result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", remote_command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"Dispatch exceeded the {timeout}s hard timeout"}

    if ssh_result.returncode != 0:
        return {
            "error": f"SSH/claude exited {ssh_result.returncode}: {ssh_result.stderr.strip()}",
            "raw_stderr": ssh_result.stderr,
        }

    json_line = None
    for line in ssh_result.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and '"type":"result"' in line:
            json_line = line
            break

    if json_line is None:
        return {
            "error": "No result JSON found in claude's output",
            "raw_stdout": ssh_result.stdout,
        }

    return json.loads(json_line)


def _snapshot_omen_worktrees() -> set:
    """
    Best-effort snapshot of every worktree path currently registered
    against the Omen's main checkout (`git worktree list --porcelain`).
    Used by dispatch_headless_task() to spot the one new path a dispatch
    just created — claude -p's own JSON result never reports its worktree
    path directly. Returns an empty set on any failure (unreachable SSH,
    non-zero exit) rather than raising; a failed snapshot just means
    worktree_path capture degrades to None for that dispatch, not a crash.
    """
    try:
        result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", f"git -C {OMEN_REPO_PATH} worktree list --porcelain"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return set()
    if result.returncode != 0:
        return set()
    return {line[len("worktree ") :].strip() for line in result.stdout.splitlines() if line.startswith("worktree ")}


def dispatch_headless_task(
    task_description: str,
    worktree_name: str | None = None,
    fuel_source: str = "auto",
) -> dict:
    """
    Dispatch one headless coding task to run via `claude -p --worktree` on
    the Omen, over SSH. Returns a dict with the real result: success,
    fuel_source (which credential actually ran — "subscription" or
    "api_key", attached to every return path, not just the success one),
    session_id (for resumability via resume_headless_task()), worktree_path/
    worktree_name (for the same resume — captured via a before/after
    `git worktree list --porcelain` diff, since claude -p's own JSON result
    never reports it; None on paths that never got far enough to create
    one), escalation (from check_escalation() — real as of 86bax0wkj), the
    model's own summary text, cost, stop_reason, and turn count. On
    failure, returns success=False with an error message and the raw
    SSH/claude output for debugging — never raises.

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
            "worktree_path": None,
            "worktree_name": None,
            "reason": pause_state.get("reason"),
            "error": "Dispatch is paused — call set_dispatch_pause(False) or "
            "`python nova_omen_dispatch.py --resume` to clear it.",
        }

    # Always pass an explicit, generated worktree name now — never the bare
    # --worktree flag — so the diff below has a known name to cross-check
    # against, and so a resume always has a real name to log alongside the
    # captured path.
    resolved_worktree_name = worktree_name or f"nova-dispatch-{uuid.uuid4().hex[:8]}"
    quoted_task = shlex.quote(task_description)
    credential_prefix = _build_credential_prefix(resolved_fuel_source)
    # Backgrounded (`&`) so `$!` captures the real claude -p PID immediately,
    # written to REMOTE_DISPATCH_PID_PATH before `wait $!` blocks for the
    # actual result -- the abort/kill switch (86bb3ceyj) targets this real
    # PID directly rather than the wrapper process, which a no-pty SSH
    # connection can't reliably signal through to the remote command.
    # `credential_prefix` is either an `env -u ...` invocation or a
    # `VAR=value` shell-assignment prefix -- neither forks an extra shell,
    # so `$!` still correctly resolves to the actual claude process, not an
    # intermediate wrapper. stdout/stderr stay inherited from the backgrounded
    # job, so `_run_claude_over_ssh()`'s result-JSON scan is unaffected.
    remote_command = (
        f"cd {OMEN_REPO_PATH} && {credential_prefix} "
        f"claude -p --worktree {resolved_worktree_name} --permission-mode acceptEdits "
        f"--output-format json {quoted_task} & "
        f"echo $! > {REMOTE_DISPATCH_PID_PATH}; wait $!"
    )

    # Safe to diff for exactly one new path because nova_scheduled_dispatch.py's
    # atomic lock file already serializes every cron-triggered dispatch — no
    # two dispatches from that path ever create worktrees concurrently. The
    # one honest gap: manual `nova_task_queue.py --dispatch` calls bypass
    # that lock, so two truly simultaneous manual dispatches could produce
    # an ambiguous diff (surfaced via worktree_capture_note below, not
    # silently guessed).
    before_worktrees = _snapshot_omen_worktrees()
    raw = _run_claude_over_ssh(remote_command)
    after_worktrees = _snapshot_omen_worktrees()
    new_worktrees = after_worktrees - before_worktrees

    worktree_capture_note = None
    if len(new_worktrees) == 1:
        worktree_path = new_worktrees.pop()
    elif not new_worktrees:
        worktree_path = None
    else:
        worktree_path = None
        worktree_capture_note = (
            f"Expected at most one new worktree, found {len(new_worktrees)}: {sorted(new_worktrees)} "
            f"— worktree_path could not be captured unambiguously."
        )

    if "error" in raw:
        result = {
            "success": False,
            "fuel_source": resolved_fuel_source,
            "worktree_path": worktree_path,
            "worktree_name": resolved_worktree_name,
            **raw,
        }
        if worktree_capture_note:
            result["worktree_capture_note"] = worktree_capture_note
        return result

    dispatch_result = {
        "success": not raw.get("is_error", False) and raw.get("stop_reason") == "end_turn",
        "fuel_source": resolved_fuel_source,
        "session_id": raw.get("session_id"),
        "summary": raw.get("result"),
        "stop_reason": raw.get("stop_reason"),
        "cost_usd": raw.get("total_cost_usd"),
        "num_turns": raw.get("num_turns"),
        "worktree_path": worktree_path,
        "worktree_name": resolved_worktree_name,
    }
    if worktree_capture_note:
        dispatch_result["worktree_capture_note"] = worktree_capture_note

    dispatch_result["escalation"] = check_escalation(dispatch_result)

    # Feed this session's real turn-level data into the same training corpus
    # nova_orchestrator.py's interactive loop writes to (agent_log.jsonl) —
    # a real round-trip happened (session_id present) whether the task
    # itself succeeded or not, and a discarded/failed run's tool-call
    # telemetry is still real data. Non-fatal: a conversion failure must
    # never take down a dispatch that already completed its real job.
    if dispatch_result["session_id"]:
        dispatch_result["agent_log_ingest"] = _ingest_transcript_into_agent_log(
            dispatch_result["session_id"], task_description
        )

    return dispatch_result


# ── Real sandboxing (86baf72qq/86barex1u groundwork) ────────────────────
#
# dispatch_headless_task() above runs `claude -p --worktree` directly over
# bare SSH on the Omen's real shell -- confirmed live, Claude Code's own
# native Bash/Read/Write/Edit tools have full, uncontained access to
# whatever the SSH user can reach (no path validation, no command
# denylist, nothing). This is the highest-risk execution path in the
# project -- fully unattended, nobody watching in real time -- and today
# it's the LEAST contained one (nova_tools.py's run_command() containment
# is a different code path, only used by nova_orchestrator.py's separate
# hand-rolled loop, never by headless dispatch). See
# docker/nova-dispatch-sandbox/Dockerfile for the sandbox image itself.
#
# Deliberate v1 scope, confirmed with Marvin before building: real
# containment for headless dispatch specifically (the highest-risk path),
# not the full 86baf72qq vision (5 container types mapped to LangGraph
# nodes, needs MCP tool-calling which doesn't exist) and not OpenHands
# (86barex1u, needs a trained local coding model which doesn't exist
# either). This function is new, additive, and opt-in -- callers choose it
# explicitly; dispatch_headless_task() above is completely unchanged and
# stays the default.

DOCKER_IMAGE = "nova-dispatch-sandbox:latest"


def _create_worktree_on_omen(worktree_name: str) -> dict:
    """
    Pre-create a git worktree on the Omen's real filesystem via plain git
    operations over SSH -- no claude/API involved yet. Needed because the
    sandboxed path below runs `claude -p` WITHOUT its own --worktree flag:
    that flag creates the worktree itself, but only on whatever filesystem
    claude's own process can see -- inside a not-yet-started container,
    nothing exists yet to create it on. Mirrors nova_orchestrator.py's
    _create_worktree() (same branch-from-origin/master semantics this
    module's own docstring already documents for the --worktree flag,
    replicated here explicitly with `git fetch origin` first since bypassing
    that flag also means bypassing whatever fetch behavior it does
    internally).

    Worktree path matches Claude Code's own --worktree convention
    (.claude/worktrees/<name> relative to the repo root) so sandboxed and
    non-sandboxed dispatch stay consistent and _snapshot_omen_worktrees()'s
    existing diff logic needs no changes to support this new path.

    Returns {"success": True, "worktree_path": ...} or
    {"success": False, "error": ...} -- never raises.
    """
    worktree_path = f"{OMEN_REPO_PATH}/.claude/worktrees/{worktree_name}"
    remote_command = (
        f"cd {OMEN_REPO_PATH} && git fetch origin && "
        f"git worktree add {shlex.quote(worktree_path)} -b {shlex.quote(worktree_name)} origin/master"
    )
    try:
        result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", remote_command],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "git worktree add timed out"}
    if result.returncode != 0:
        return {"success": False, "error": f"git worktree add failed: {result.stderr.strip()}"}
    return {"success": True, "worktree_path": worktree_path}


def dispatch_headless_task_sandboxed(
    task_description: str,
    worktree_name: str | None = None,
) -> dict:
    """
    Real-sandboxed variant of dispatch_headless_task() -- runs `claude -p`
    INSIDE a Docker container on the Omen instead of directly on its bare
    shell.

    Mount design (see docker/nova-dispatch-sandbox/Dockerfile for the full
    reasoning): only {OMEN_REPO_PATH}/.git (rw -- git needs this for any
    worktree operation, since commits/diffs write to the main repo's shared
    object store) and this task's own pre-created worktree directory (rw)
    are mounted, both at their real host-matching absolute paths inside the
    container -- required, since a worktree's own `.git` FILE contains an
    absolute `gitdir:` pointer back to the main repo, so the path has to
    match exactly on both sides or git breaks. Nothing else under
    OMEN_REPO_PATH is visible inside the container at all: no `.env`, no
    `nova_state.db`, no other worktrees, no other source files -- verified
    live with a real negative-containment check (see the commit/PR this
    shipped in), not just designed and assumed.

    Also mounts the Omen user's real `~/.claude` directory (rw). Real,
    deliberate trade-off, stated honestly rather than silently done: without
    this, Claude's own session transcript is written inside the container's
    ephemeral filesystem and is lost the instant the container exits
    (`--rm`), silently breaking _ingest_transcript_into_agent_log()'s
    downstream training-data capture for every sandboxed dispatch. The
    container can therefore read Claude Code's own config/session-history
    directory -- a real, narrower category of access than this project's
    application secrets (.env/nova_state.db), which stay genuinely
    unreachable -- but it is not full isolation from every file on the Omen.

    Real, deliberate v1 scope boundary: only supports the metered
    ANTHROPIC_API_KEY credential ("api_key" fuel source), never the
    "subscription" login -- that needs the Omen user's stored Claude Code
    OAuth credentials, real broader auth material this sandbox deliberately
    does not widen access for, rather than silently reaching for it just to
    preserve today's dual-fuel flexibility before that trade-off has been
    asked about directly.

    Never raises. Returns the same result shape as dispatch_headless_task()
    (plus "sandboxed": True) on success; {"success": False, "error": ...}
    on any failure.
    """
    pause_state = is_dispatch_paused()
    if pause_state["paused"]:
        return {
            "success": False,
            "paused": True,
            "worktree_path": None,
            "worktree_name": None,
            "reason": pause_state.get("reason"),
            "error": "Dispatch is paused — call set_dispatch_pause(False) or "
            "`python nova_omen_dispatch.py --resume` to clear it.",
        }

    resolved_worktree_name = worktree_name or f"nova-dispatch-{uuid.uuid4().hex[:8]}"

    worktree_result = _create_worktree_on_omen(resolved_worktree_name)
    if not worktree_result["success"]:
        return {
            "success": False,
            "fuel_source": "api_key",
            "worktree_path": None,
            "worktree_name": resolved_worktree_name,
            "error": worktree_result["error"],
        }
    worktree_path = worktree_result["worktree_path"]

    # Extract only ANTHROPIC_API_KEY from .env via the Omen's own venv +
    # python-dotenv -- same narrow-extraction discipline as
    # _build_credential_prefix()'s "api_key" branch, never a blanket
    # `source .env` (would leak CLICKUP_API_KEY/RUNPOD_API_KEY into the
    # container for no reason).
    dotenv_code = (
        f"from dotenv import dotenv_values; print(dotenv_values('{OMEN_ENV_PATH}').get('ANTHROPIC_API_KEY', ''))"
    )
    quoted_task = shlex.quote(task_description)
    omen_home = f"/home/{OMEN_USER}"
    remote_command = (
        f"API_KEY=$({OMEN_VENV_PYTHON} -c {shlex.quote(dotenv_code)}) && "
        # Clear any stale leftover with this fixed name first -- a crashed
        # prior run could otherwise leave `docker run --name` refusing to
        # start with "name already in use." Mirrors the lock file's own
        # stale-PID self-healing philosophy. Silenced/best-effort: nothing
        # to clean up is the normal case, not an error.
        f"docker rm -f {SANDBOXED_CONTAINER_NAME} >/dev/null 2>&1; "
        # --user, resolved live via `id`, not a hardcoded UID -- without this
        # the container runs as root by default, and any file it writes onto
        # a mounted host volume (the transcript under ~/.claude/projects/,
        # confirmed live) comes out root-owned and unreadable to the real
        # marvinroyal5 account afterward, silently breaking
        # _ingest_transcript_into_agent_log()'s downstream read. Found via a
        # real live test, not anticipated in the original design.
        #
        # --name is fixed (SANDBOXED_CONTAINER_NAME), not per-task -- the
        # abort/kill switch (86bb3ceyj) targets it directly via `docker
        # kill`, safe because nova_scheduled_dispatch.py's lock already
        # guarantees at most one sandboxed dispatch runs at a time.
        f"docker run --rm --name {SANDBOXED_CONTAINER_NAME} --user $(id -u):$(id -g) "
        f'-e HOME={shlex.quote(omen_home)} -e ANTHROPIC_API_KEY="$API_KEY" '
        f"-v {shlex.quote(omen_home)}/.claude:{shlex.quote(omen_home)}/.claude "
        f"-v {shlex.quote(OMEN_REPO_PATH)}/.git:{shlex.quote(OMEN_REPO_PATH)}/.git "
        f"-v {shlex.quote(worktree_path)}:{shlex.quote(worktree_path)} "
        f"-w {shlex.quote(worktree_path)} "
        f"{DOCKER_IMAGE} "
        # bypassPermissions, not acceptEdits -- confirmed live 2026-07-25 that
        # acceptEdits only auto-approves file-edit tools, never Bash: a real
        # dispatch against this exact container returned a
        # permission_denials: [{"tool_name": "Bash", ...}] entry for a plain
        # `python3 -c "print(2+2)"` call, meaning no sandboxed dispatch has
        # ever been able to run a shell command, only edit files. Safe to
        # bypass here specifically because the Docker mount boundary above is
        # the real containment (verified: .env/nova_state.db unreachable) --
        # this is exactly the "sandbox with no internet-reachable secrets"
        # case the flag's own --help text names. Deliberately NOT applied to
        # dispatch_headless_task()'s bare-SSH command below or
        # resume_headless_task()'s -- neither runs inside a container, so
        # bypassing permissions there would mean zero prompts AND zero
        # containment.
        f"claude -p --permission-mode bypassPermissions --output-format json {quoted_task}"
    )

    raw = _run_claude_over_ssh(remote_command)

    if "error" in raw:
        return {
            "success": False,
            "fuel_source": "api_key",
            "worktree_path": worktree_path,
            "worktree_name": resolved_worktree_name,
            **raw,
        }

    dispatch_result = {
        "success": not raw.get("is_error", False) and raw.get("stop_reason") == "end_turn",
        "fuel_source": "api_key",
        "session_id": raw.get("session_id"),
        "summary": raw.get("result"),
        "stop_reason": raw.get("stop_reason"),
        "cost_usd": raw.get("total_cost_usd"),
        "num_turns": raw.get("num_turns"),
        "worktree_path": worktree_path,
        "worktree_name": resolved_worktree_name,
        "sandboxed": True,
    }

    dispatch_result["escalation"] = check_escalation(dispatch_result)

    if dispatch_result["session_id"]:
        dispatch_result["agent_log_ingest"] = _ingest_transcript_into_agent_log(
            dispatch_result["session_id"], task_description
        )

    return dispatch_result


def resume_headless_task(
    worktree_path: str,
    session_id: str,
    answer_text: str,
    fuel_source: str = "auto",
) -> dict:
    """
    Resume a session that previously escalated (86bax0wkj) — cd's into the
    exact original worktree (worktree_path, as captured by
    dispatch_headless_task()) and runs `claude -p --resume <session_id>`
    with Marvin's answer, continuing that same session rather than starting
    a new one. No --worktree flag at all: passing one would create a fresh
    worktree instead of continuing this one.

    Deliberately does NOT call is_dispatch_paused() — confirmed with
    Marvin: answering a direct question you were asked is a different act
    than a new autonomous run starting while he's mid-build, so the global
    dispatch-pause switch does not block a resume. This is a deliberate
    asymmetry with dispatch_headless_task(), not an oversight.

    Re-runs check_escalation() on the result, so a second escalation on the
    same task is handled identically to the first, uncapped — no special-
    casing for "this is already a resume."

    fuel_source: "auto" (default) resolves independently via
    choose_fuel_source() — a resume is a genuinely separate invocation and
    can land on either credential regardless of what the original dispatch
    used.
    """
    resolved_fuel_source = choose_fuel_source() if fuel_source == "auto" else fuel_source
    credential_prefix = _build_credential_prefix(resolved_fuel_source)
    quoted_answer = shlex.quote(answer_text)
    remote_command = (
        f"cd {shlex.quote(worktree_path)} && {credential_prefix} "
        f"claude -p --resume {shlex.quote(session_id)} --permission-mode acceptEdits "
        f"--output-format json {quoted_answer}"
    )

    raw = _run_claude_over_ssh(remote_command)
    if "error" in raw:
        return {
            "success": False,
            "fuel_source": resolved_fuel_source,
            "worktree_path": worktree_path,
            **raw,
        }

    result = {
        "success": not raw.get("is_error", False) and raw.get("stop_reason") == "end_turn",
        "fuel_source": resolved_fuel_source,
        "session_id": raw.get("session_id"),
        "summary": raw.get("result"),
        "stop_reason": raw.get("stop_reason"),
        "cost_usd": raw.get("total_cost_usd"),
        "num_turns": raw.get("num_turns"),
        "worktree_path": worktree_path,
    }
    result["escalation"] = check_escalation(result)

    # See _ingest_transcript_into_agent_log()'s own docstring for the one
    # accepted gap here: answer_text stands in for task_description on
    # resumed turns since the original task string isn't in scope here.
    if result["session_id"]:
        result["agent_log_ingest"] = _ingest_transcript_into_agent_log(result["session_id"], answer_text)

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dispatch a headless coding task to the Omen.")
    parser.add_argument("task", nargs="?", help="Task description. Omitted with --pause/--resume.")
    parser.add_argument("--pause", metavar="REASON", help="Pause dispatch until --resume is called.")
    parser.add_argument("--resume", action="store_true", help="Clear a previously set pause.")
    parser.add_argument(
        "--fuel-source",
        choices=["auto", "subscription", "api_key"],
        default="auto",
        help="Which credential to use ('auto' checks the real activity profile; default: auto).",
    )
    args = parser.parse_args()

    if args.pause is not None:
        print(json.dumps(set_dispatch_pause(True, args.pause), indent=2))
    elif args.resume:
        print(json.dumps(set_dispatch_pause(False), indent=2))
    else:
        task = args.task or "Reply with exactly: dispatch module smoke test ok"
        print(json.dumps(dispatch_headless_task(task, fuel_source=args.fuel_source), indent=2))
