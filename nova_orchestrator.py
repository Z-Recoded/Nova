# nova_orchestrator.py
# Nova's coding sub-agent — the "Nova building itself" loop.
#
# Interim v1: driven by the Claude API (not yet a locally fine-tuned model),
# isolated by a disposable git worktree + branch rather than Docker. Every
# task gets its own worktree; this module never merges or deletes it —
# a human always reviews the diff and merges by hand. See CLAUDE.md
# Phase 3.5 for why this sequencing was chosen.
#
# Usage:
#   from nova_orchestrator import run_coding_task
#   result = run_coding_task("Add a GET /healthz endpoint that returns {'status': 'ok'}")

import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from nova_backend_profiles import CLAUDE_PROFILE
from nova_completion_gate import check_ground_truth_completion, extract_task_requirements
from nova_config import (
    get_approval_gate_patterns,
    get_approval_gate_poll_interval_seconds,
    get_approval_gate_timeout_seconds,
    is_framework_integration_enabled,
    is_pre_action_approval_gate_enabled,
)
from nova_langfuse_client import log_gate_result, log_turn
from nova_notify import send_notification
from nova_skills import get_skill_version, load_skill
from nova_state import get_state, write_state
from nova_token_budget import get_budget_status, record_usage
from nova_tool_call_log import log_tool_call
from nova_tools import file_replace, list_files, read_file, run_command, write_file

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ─────────────────────────────────────────────────────
# Resolved relative to this file's own location, not hardcoded to the Aero's
# Windows path -- same bug class already fixed elsewhere in this project
# (86bb1pkpb). NOVA_AGENT_WORKTREES_ROOT wasn't in the original grep audit
# (it doesn't contain the literal "C:/Nova" string) but is the identical bug
# shape -- a sibling directory hardcoded to a Windows path -- so fixed here too.
NOVA_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
NOVA_AGENT_WORKTREES_ROOT = os.path.join(os.path.dirname(NOVA_REPO_ROOT), "nova-agent-worktrees")

NOVA_AGENT_MODEL = "claude-sonnet-5"
# Raised from 15: the first two real tasks both hit this cap despite doing
# legitimate, correct work — verification/debugging (syntax checks, fixing
# an encoding regression) reliably eats several turns on top of the actual
# read/write/verify cycle.
NOVA_AGENT_MAX_TURNS = 25
# Generous headroom: a write_file call has to carry a whole file's contents
# as its tool-input argument, which can run several thousand tokens on its
# own for a file the size of nova_api.py.
NOVA_AGENT_MAX_TOKENS = 8192

LOGS_DIR = os.path.join(NOVA_REPO_ROOT, "logs")
AGENT_LOG_PATH = f"{LOGS_DIR}/agent_log.jsonl"
TASK_OUTCOMES_LOG_PATH = f"{LOGS_DIR}/agent_task_outcomes.jsonl"
CODING_REVIEW_LOG_PATH = f"{LOGS_DIR}/coding_review_log.jsonl"
GROUND_TRUTH_GATE_LOG_PATH = f"{LOGS_DIR}/ground_truth_gate_log.jsonl"

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": "Read a text file's contents, relative to the task's worktree root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the worktree root."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Create or overwrite a text file, relative to the task's worktree root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the worktree root."},
                "content": {"type": "string", "description": "Full file contents to write."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "file_replace",
        "description": (
            "Replace a single unique occurrence of old_str with new_str in an "
            "existing file, relative to the task's worktree root. old_str must "
            "appear exactly once. Prefer this over write_file for edits to "
            "existing files — it sends only the changed text as output instead "
            "of the whole file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the worktree root."},
                "old_str": {
                    "type": "string",
                    "description": "Exact text to replace — must appear exactly once in the file.",
                },  # noqa: E501
                "new_str": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": "list_files",
        "description": "List files under a directory, relative to the task's worktree root.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path, relative to the worktree root. Use '.' for the whole worktree.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_command",
        "description": "Run a shell command with cwd pinned to the task's worktree root (e.g. running tests).",
        "input_schema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "description": "Shell command to run."},
            },
            "required": ["cmd"],
        },
    },
]


# ── Worktree setup ───────────────────────────────────────────────


def _slugify(task_description: str) -> str:
    """Turn a task description into a short, unique, filesystem-safe slug."""
    base = re.sub(r"[^a-z0-9]+", "-", task_description.lower()).strip("-")[:40]
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{base}-{timestamp}"


def _create_worktree(slug: str) -> tuple[Path, str]:
    """
    Create a disposable git worktree + branch for one coding task, based on
    the current master tip. Returns (worktree_path, branch_name).
    """
    branch_name = f"nova-agent/{slug}"
    worktree_path = Path(NOVA_AGENT_WORKTREES_ROOT) / slug
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "-b", branch_name],
        cwd=NOVA_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_path, branch_name


COMMIT_SUBJECT_MAX_CHARS = 72


def _summarize_task(task_description: str) -> str:
    """
    Shorten a task description to a git-subject-line length, cutting at the
    last whole word rather than mid-word, with an ellipsis if it was cut.
    """
    if len(task_description) <= COMMIT_SUBJECT_MAX_CHARS:
        return task_description
    truncated = task_description[:COMMIT_SUBJECT_MAX_CHARS].rsplit(" ", 1)[0]
    return f"{truncated}..."


def _commit_worktree_changes(root: str, task_description: str, note: str = "") -> bool:
    """
    Stage and commit whatever the agent changed in its worktree, so the
    branch actually has real content for a human to merge (not just
    uncommitted working-tree edits sitting in a disposable directory).
    Returns False if there was nothing to commit. `note`, if given, is
    appended as an extra commit body line (e.g. a budget-halt marker).
    """
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True)
    if not status.stdout.strip():
        return False

    commit_message = (
        f"{_summarize_task(task_description)}\n\nWritten by nova_orchestrator.py (Nova's coding sub-agent)."  # noqa: E501
    )
    if note:
        commit_message += f"\n\n{note}"
    subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return True


def _git_diff_against_master(root: str) -> str:
    """
    Return the full diff of this worktree's branch against master. Runs
    `git add -N` first so newly-created (untracked) files show up as real
    additions in the diff instead of being silently omitted -- `git diff`
    alone ignores untracked paths entirely. This doesn't stage file
    content (just the path), so _commit_worktree_changes()'s later
    `git add -A` still commits everything normally.
    """
    subprocess.run(["git", "add", "-N", "."], cwd=root, capture_output=True, text=True)
    result = subprocess.run(
        ["git", "diff", "master"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


# ── Agent loop ───────────────────────────────────────────────────


def _build_system_prompt(root: str) -> str:
    """System prompt: the project's own coding standards, plus tool-use guidance."""
    claude_md = read_file("CLAUDE.md", root)
    return (
        "You are Nova's coding sub-agent, operating inside a disposable git "
        "worktree. You have file read/write/list tools and a run_command tool, "
        "all scoped to this worktree only — nothing you do here touches the "
        "live Nova codebase until a human reviews and merges your branch. "
        "run_command executes via Git Bash, so use Unix-style commands (ls, "
        "grep, cat), not cmd.exe/PowerShell syntax. Your worktree has no "
        "virtualenv of its own (nova-env/ isn't git-tracked) — plain `python` "
        "and `pip` in run_command already resolve to the live project's venv, "
        "so just use them directly; no need to hunt for an interpreter path. "
        "run_command rejects any `cd` outside this worktree — PATH is locked "
        "to the live venv's Scripts dir (for python/pip), Git Bash's own bin "
        "dirs, and this worktree root, so don't try to cd elsewhere. All "
        "file edits must go through write_file or file_replace, scoped to "
        "this worktree. For edits to a file that "
        "already exists, prefer file_replace over write_file — it only sends "
        "the changed old_str/new_str pair as output instead of the whole "
        "file, which matters for larger files like nova_api.py or "
        "nova_orchestrator.py. old_str must match exactly once; if it "
        "doesn't, either pick a larger, more specific old_str or fall back "
        "to write_file for that edit. Reserve write_file for brand-new "
        "files.\n\n"
        "Read and follow the project's own coding standards below exactly:\n\n"
        f"{claude_md}\n\n"
        "---\n"
        "You have a limited number of turns. Prefer writing files directly "
        "with write_file over exploring the shell environment — don't spend "
        "turns probing tool availability or paths defensively; write the "
        "code, then verify it with one focused run_command call. Work the "
        "task to completion within your available turns. When finished, "
        "reply with a short plain-text summary of what you changed and why "
        "— no more tool calls after that summary."
    )


# Aero-lane-only, in-memory, process-lifetime counter backing the (not yet
# wired in) max_files_per_turn gate trigger — resets on process restart, no
# persistence. Accepted gap for this narrow slice (86bb3ceym); file-count
# gating is a deliberate fast-follow once the command-pattern trigger below
# is proven live, not part of this cut.
_session_file_edit_counts: dict[str, int] = {}


def _approval_gate_reason(name: str, tool_input: dict, session_id: str | None) -> str | None:
    """
    None means silently allow. Any other value is the human-readable reason
    a tool call is being paused for approval (86bb3ceym). Only run_command
    is gated in this first cut — read_file/list_files/write_file/file_replace
    are left alone (max_files_per_turn is designed but deliberately not
    wired in yet, see _session_file_edit_counts above). This is a distinct,
    less-severe tier than nova_tools.py's DANGEROUS_COMMAND_PATTERNS hard
    denylist (rm -rf, git push, etc.) — those are always refused and never
    reach here; these patterns are risky-but-sometimes-legitimate actions
    that today execute silently with zero friction.
    """
    if name != "run_command":
        return None
    cmd_lower = tool_input.get("cmd", "").lower()
    for pattern in get_approval_gate_patterns():
        if pattern.lower() in cmd_lower:
            return f"matched approval-gate pattern '{pattern}'"
    return None


def _request_tool_approval(
    name: str, tool_input: dict, root: str, session_id: str | None, task_description: str, reason: str
) -> dict:
    """
    Register a pending tool-call approval directly in nova_state.db and
    block (sleep-poll) until Marvin decides via the Controller's tool
    approval card, or the configured timeout elapses. No HTTP hop needed —
    unlike the cross-machine escalation flow, POST /agent/task runs
    synchronously in-process on the Aero's own nova_api.py, the same
    machine/process whose nova_state.db this call is already writing to.

    Fails CLOSED on timeout (status becomes "timed_out", treated as a
    denial by the caller) — same fail-toward-restrictive instinct as
    nova_escalation.is_dispatch_paused() failing toward paused=True.
    """
    approval_id = str(uuid.uuid4())
    pending = get_state("system", "pending_tool_approvals") or {}
    pending.pop("_updated_at", None)
    pending[approval_id] = {
        "approval_id": approval_id,
        "lane": "interactive",
        "session_id": session_id,
        "task_description": task_description[:200],
        "tool_name": name,
        "tool_input": tool_input,
        "reason": reason,
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "decided_at": None,
        "comment": None,
        "root": root,
    }
    write_state("system", "pending_tool_approvals", pending)

    send_notification(
        title="Nova: approval needed",
        message=f"{name}: {reason}",
        tags="warning",
        priority="high",
    )

    timeout_seconds = get_approval_gate_timeout_seconds()
    poll_interval = get_approval_gate_poll_interval_seconds()
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        current = get_state("system", "pending_tool_approvals") or {}
        record = current.get(approval_id)
        if record and record["status"] != "pending":
            return record

    current = get_state("system", "pending_tool_approvals") or {}
    current.pop("_updated_at", None)
    record = current.get(approval_id, pending[approval_id])
    record["status"] = "timed_out"
    record["decided_at"] = datetime.now().isoformat(timespec="seconds")
    current[approval_id] = record
    write_state("system", "pending_tool_approvals", current)
    return record


def _execute_tool(
    name: str, tool_input: dict, root: str, session_id: str | None = None, task_description: str = ""
) -> dict:
    """
    Dispatch one Claude tool_use call to the matching nova_tools function.
    Logs every call to tool_call_log.jsonl (86bawntpb) regardless of caller —
    session_id is optional so this stays a safe drop-in for callers (e.g.
    nova_orchestrator_graph.py) that don't pass one yet.

    When pre_action_approval_gate is enabled, a matching call pauses here
    (before dispatch to the real nova_tools function) and blocks on a human
    decision — see _request_tool_approval(). This covers the Aero
    interactive lane only; the Omen headless dispatch lane runs `claude -p`
    directly over SSH and never calls this function at all (see CLAUDE.md's
    Pre-Action Approval Gate subsection for the full scoping note).
    """
    start = time.monotonic()
    try:
        if is_pre_action_approval_gate_enabled():
            reason = _approval_gate_reason(name, tool_input, session_id)
            if reason:
                decision = _request_tool_approval(name, tool_input, root, session_id, task_description, reason)
                if decision.get("status") != "approved":
                    latency_ms = (time.monotonic() - start) * 1000
                    detail = f"approval_gate:{decision.get('status')} — {reason}"
                    log_tool_call(
                        agent="nova_orchestrator",
                        session_id=session_id,
                        tool=name,
                        args=tool_input,
                        result="error",
                        error_detail=detail,
                        latency_ms=round(latency_ms, 1),
                    )
                    return {
                        "content": f"Tool call blocked by approval gate ({decision.get('status')}): {reason}",
                        "is_error": True,
                    }
        if name == "read_file":
            outcome = {"content": read_file(tool_input["path"], root)}
        elif name == "write_file":
            write_file(tool_input["path"], tool_input["content"], root)
            outcome = {"content": f"Wrote {tool_input['path']}"}
        elif name == "file_replace":
            file_replace(tool_input["path"], tool_input["old_str"], tool_input["new_str"], root)
            outcome = {"content": f"Replaced content in {tool_input['path']}"}
        elif name == "list_files":
            outcome = {"content": "\n".join(list_files(tool_input["path"], root))}
        elif name == "run_command":
            result = run_command(tool_input["cmd"], root)
            outcome = {"content": json.dumps(result)}
        else:
            outcome = {"content": f"Unknown tool '{name}'", "is_error": True}
    except Exception as e:
        outcome = {"content": str(e), "is_error": True}

    latency_ms = (time.monotonic() - start) * 1000
    log_tool_call(
        agent="nova_orchestrator",
        session_id=session_id,
        tool=name,
        args=tool_input,
        result="error" if outcome.get("is_error") else "success",
        error_detail=outcome["content"] if outcome.get("is_error") else None,
        latency_ms=round(latency_ms, 1),
    )
    return outcome


def _log_agent_turn(
    slug: str, branch: str, turn: int, task: str, response, skill_category: str | None, skill_version: str | None
) -> None:
    """Append one turn of an agent task to agent_log.jsonl (JSONL, mirrors nova_log.py)."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    tool_calls = [{"name": block.name, "input": block.input} for block in response.content if block.type == "tool_use"]
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "task_slug": slug,
        "branch": branch,
        "turn": turn,
        "task": task,
        "skill_category": skill_category,
        "skill_version": skill_version,
        "stop_reason": response.stop_reason,
        "tool_calls": tool_calls,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": response.usage.cache_creation_input_tokens,
        "cache_read_input_tokens": response.usage.cache_read_input_tokens,
        "model": response.model,
        "backend_profile": CLAUDE_PROFILE.name,
    }
    with open(AGENT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_coding_task(task_description: str, category: str | None = None) -> dict:
    """
    Run one coding task end-to-end: spin up a disposable worktree, drive a
    Claude-backed tool-use loop against it, log every turn, and return a
    diff summary for human review. Never merges or deletes the worktree.

    `category`, if given, selects a Nova Skills Library file (see
    nova_skills.py) to prepend to the task's context when skill_injection
    is enabled in nova_config.json — orients the model with precise,
    compact conventions instead of the model re-deriving them from
    CLAUDE.md alone. No effect if the flag is off or category is None.
    """
    runpod_enabled = is_framework_integration_enabled("runpod_coding_agent")
    devstral_enabled = is_framework_integration_enabled("devstral_coding_agent")
    client = None
    if not runpod_enabled and not devstral_enabled:
        # Neither RunPod backend has an Anthropic SDK client and neither
        # needs ANTHROPIC_API_KEY at all -- only construct/require it for
        # the Claude-backed paths (inline loop or LangGraph).
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise OSError(
                "ANTHROPIC_API_KEY environment variable is not set. Export it before calling run_coding_task()."
            )
        client = anthropic.Anthropic(api_key=api_key)
    budget_gate_enabled = is_framework_integration_enabled("token_budget_governor")
    skill_injection_enabled = is_framework_integration_enabled("skill_injection")

    slug = _slugify(task_description)
    worktree_path, branch_name = _create_worktree(slug)
    root = str(worktree_path)

    # Extracted once, up front, only for the RunPod-family lanes -- the
    # Claude lane has never shown the scope-violation failure mode this
    # backs (86bb72wd5), so it doesn't pay for an extra Claude API call it
    # doesn't need. Reused both for the task-scoped file allowlist guard
    # (below) and the ground-truth completion gate at the end of this
    # function, so the extraction only ever runs once per task, not twice.
    requirements = extract_task_requirements(task_description) if (runpod_enabled or devstral_enabled) else None

    if runpod_enabled or devstral_enabled:
        # The full system prompt (_build_system_prompt) bakes in CLAUDE.md
        # verbatim -- ~14.5K tokens on its own, real bug found live: this
        # left too little of the RunPod endpoint's 32768-token context
        # window for actual task work, hard-failing on anything beyond a
        # couple of turns. A condensed, RunPod-family standards summary
        # replaces it for these backends only -- the Claude/LangGraph paths
        # are untouched. run_via_devstral() actually builds its own system
        # prompt internally (native tool-calling needs no prompted <tools>
        # format instructions -- see build_devstral_system_prompt()'s own
        # comment), so this value is discarded on that path; computed the
        # same cheap way regardless rather than adding a third branch here.
        from nova_orchestrator_runpod import build_condensed_system_prompt

        system_prompt = build_condensed_system_prompt()
    else:
        system_prompt = _build_system_prompt(root)

    skill_category = None
    skill_version = None
    first_message_content = task_description
    if skill_injection_enabled and category:
        skill_content = load_skill(category)
        if skill_content:
            skill_category = category
            skill_version = get_skill_version(category)
            first_message_content = f"{skill_content}\n\n---\n\n{task_description}"

    messages = [{"role": "user", "content": first_message_content}]

    started_at = time.time()
    final_status = "incomplete"
    turns_used = 0

    if runpod_enabled:
        # Lazy import, same rationale as the langgraph/devstral branches
        # below: this module (and nova_remote_inference) is only ever
        # imported when the flag is on. All three of RunPod/Devstral/
        # LangGraph are mutually exclusive alternate backends, not
        # stackable -- checked first since it needs no Anthropic client at
        # all (client is None on this path).
        from nova_orchestrator_runpod import CODING_AGENT_MAX_OUTPUT_TOKENS, run_via_runpod

        final_status, turns_used = run_via_runpod(
            system_prompt,
            messages,
            root,
            slug,
            branch_name,
            task_description,
            skill_category,
            skill_version,
            budget_gate_enabled,
            NOVA_AGENT_MAX_TURNS,
            CODING_AGENT_MAX_OUTPUT_TOKENS,
            requirements,
        )
    elif devstral_enabled:
        # Lazy import, same rationale as the runpod branch above -- this
        # module (and nova_remote_inference_native_tools) is only ever
        # imported when this flag is on.
        from nova_orchestrator_devstral import CODING_AGENT_MAX_OUTPUT_TOKENS, run_via_devstral

        final_status, turns_used = run_via_devstral(
            system_prompt,
            messages,
            root,
            slug,
            branch_name,
            task_description,
            skill_category,
            skill_version,
            budget_gate_enabled,
            NOVA_AGENT_MAX_TURNS,
            CODING_AGENT_MAX_OUTPUT_TOKENS,
            requirements,
        )
    elif is_framework_integration_enabled("langgraph_orchestration"):
        # Lazy import: langgraph is only ever imported when this flag is on,
        # so a missing/broken install can't affect Nova while the feature is
        # disabled (the default). See nova_orchestrator_graph.py for the
        # graph itself — same turn-by-turn behavior as the inline loop below,
        # just expressed as LangGraph nodes/edges instead.
        from nova_orchestrator_graph import run_via_langgraph

        final_status, turns_used = run_via_langgraph(
            client,
            system_prompt,
            messages,
            root,
            slug,
            branch_name,
            task_description,
            skill_category,
            skill_version,
            budget_gate_enabled,
            NOVA_AGENT_MAX_TURNS,
            NOVA_AGENT_MODEL,
            NOVA_AGENT_MAX_TOKENS,
            _log_agent_turn,
            _execute_tool,
        )
    else:
        for turn in range(1, NOVA_AGENT_MAX_TURNS + 1):
            if budget_gate_enabled and get_budget_status().get("mode") == "halt":
                # Checked at the top of the loop, before any further API call —
                # this means no new tool_use can be proposed at all once halted,
                # satisfying "don't start a new file edit once halted" without
                # needing to interrupt a turn already in flight (turns are
                # atomic: we only ever see a turn after the full response
                # arrives, never mid-generation).
                final_status = "stopped_budget_halt"
                break

            turns_used = turn
            response = client.messages.create(
                model=NOVA_AGENT_MODEL,
                max_tokens=NOVA_AGENT_MAX_TOKENS,
                # cache_control: system_prompt (the full CLAUDE.md contents) is
                # identical every turn of this loop — caching it turns turns 2+
                # into cheap cache reads instead of full-price resends.
                system=[
                    {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
                ],
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            _log_agent_turn(slug, branch_name, turn, task_description, response, skill_category, skill_version)
            text_content = "".join(block.text for block in response.content if block.type == "text")
            tool_calls_for_trace = [
                {"name": block.name, "input": block.input} for block in response.content if block.type == "tool_use"
            ]
            # logprobs always None here -- Claude's API exposes no
            # token-level logprobs, unlike the self-hosted vLLM backends
            # (see log_turn()'s own docstring). cost_usd also None -- this
            # lane tracks spend via nova_token_budget's token-based budget
            # model (record_usage() below), not a per-call dollar figure.
            log_turn(
                branch_name,
                turn,
                CLAUDE_PROFILE.name,
                response.model,
                text_content,
                tool_calls_for_trace,
                response.usage.input_tokens,
                response.usage.output_tokens,
            )
            if budget_gate_enabled:
                record_usage(response.usage)

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                final_status = "completed"
                break

            if response.stop_reason != "tool_use":
                # e.g. "max_tokens" — the response (possibly mid tool-call) got cut
                # off. Executing a truncated tool call could apply garbage, so stop
                # and surface it honestly rather than silently treating it as done.
                final_status = f"stopped_{response.stop_reason}"
                break

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                result = _execute_tool(
                    block.name, block.input, root, session_id=slug, task_description=task_description
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result["content"],
                        "is_error": result.get("is_error", False),
                    }
                )
            messages.append({"role": "user", "content": tool_results})
        else:
            final_status = "max_turns_reached"

    elapsed_s = round(time.time() - started_at, 1)
    diff = _git_diff_against_master(root)
    budget_status = get_budget_status() if budget_gate_enabled else {"enabled": False}

    # Ground-truth completion gate (86bb71x39) -- runs for every backend
    # (Claude, LangGraph, RunPod alike), right after the diff is known and
    # before anything downstream trusts final_status at face value. Never
    # blocks the commit itself -- see nova_completion_gate.py's own header
    # for why, and CLAUDE.md Section 8 for the "Marvin reviews every diff
    # by hand" standing rule this design leans on.
    gate_result = check_ground_truth_completion(diff, task_description, root, requirements=requirements)
    _log_ground_truth_gate(branch_name, task_description, gate_result)

    # RunPod/Qwen writes, Claude reviews (2026-07-27 review-split decision) --
    # only meaningful when the RunPod backend actually wrote this diff, so
    # gated on both flags together. Never runs for the Claude-backed path
    # (reviewing its own output would be redundant) or the LangGraph path.
    review = None
    if runpod_enabled and is_framework_integration_enabled("coding_review_pass"):
        review = _review_coding_diff(diff, task_description)
        _log_coding_review(branch_name, task_description, diff, review)

    commit_note_parts = []
    if final_status == "stopped_budget_halt":
        commit_note_parts.append(
            f"[budget-halt] stopped at {budget_status.get('session_pct')}% session budget, task left {final_status}"
        )
    if not gate_result["passed"]:
        commit_note_parts.append(f"[ground-truth-fail] {'; '.join(gate_result['hard_fails'])}")
    if review is not None and not review["approved"]:
        commit_note_parts.append(f"[review-flagged] {review['summary']} — issues: {'; '.join(review['issues'])}")
    commit_note = " ".join(commit_note_parts)
    committed = _commit_worktree_changes(root, task_description, note=commit_note)

    if final_status == "stopped_budget_halt":
        record_task_outcome(
            branch_name,
            "budget_halt",
            note=f"Stopped automatically at {budget_status.get('session_pct')}% session budget.",
        )

    return {
        "task": task_description,
        "worktree_path": root,
        "branch": branch_name,
        "status": final_status,
        "turns_used": turns_used,
        "elapsed_s": elapsed_s,
        "committed": committed,
        "diff": diff,
        "review": review,
        "budget_status": budget_status,
        "next_steps": (
            f"Review: git diff master...{branch_name} (from C:/Nova). "
            f"Merge when satisfied: git merge {branch_name}. "
            f"Then remove the worktree: git worktree remove {root}"
        )
        if committed
        else "Nothing was changed — no commit made, nothing to merge.",
    }


def _review_coding_diff(diff: str, task_description: str) -> dict:
    """
    Single non-agentic Claude call reviewing a RunPod-backed coding task's
    final diff before it's committed to its (still-disposable, still
    human-reviewed-before-merge) worktree branch. Mirrors
    nova_task_queue.propose_tier()'s existing pattern exactly: a plain
    client.messages.create() call, no tool use, no second turn loop.
    Part of Marvin's 2026-07-27 review-split decision (RunPod/Qwen writes,
    Claude reviews) -- see project_coding_agent_review_split_decision.md.

    Deliberately does NOT block the commit or re-enter the turn loop on a
    negative verdict: a worktree commit here is not a merge -- Marvin
    already reviews every diff by hand before that (see run_coding_task()'s
    own "next_steps" field). This review's job is to make that human pass
    faster and to generate real review-labeled data toward a future
    fine-tuning dataset, not to gate anything by itself yet.

    Isolation guarantee, confirmed with Marvin before building this: this
    call is never given a `tools` argument, so it has no way to write
    files even if it wanted to -- it can only return text, which is parsed
    as JSON below and never re-applied to the worktree. If this function
    is ever extended with tool access later, that guarantee needs to be
    re-verified explicitly, not assumed.

    Returns {"approved": bool, "issues": list[str], "summary": str}.
    Fails toward NOT approved (with an issue explaining why) on a missing
    API key or a parse/API failure -- same fail-toward-restrictive instinct
    as propose_tier()'s own fail-toward-"manual only". An empty diff is
    trivially approved (nothing to review).
    """
    if not diff.strip():
        return {"approved": True, "issues": [], "summary": "No changes to review."}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"approved": False, "issues": ["ANTHROPIC_API_KEY not set — review could not run."], "summary": ""}

    system = (
        "You are code-reviewing a diff produced by a different, less reliable coding model "
        "(Qwen2.5-Coder-32B) working inside a disposable git worktree. A human always reviews "
        "and merges by hand afterward -- your job is to catch real defects early, not to gate "
        "the commit. Known recurring failure modes to check for specifically: leftover "
        "duplicate/dead code left behind after a partial edit, an incomplete multi-file change "
        "(edited one file but left a dependent file inconsistent), and any change that doesn't "
        "actually address the stated task. Respond with ONLY a JSON object, no other text, in "
        'exactly this shape: {"approved": true|false, "issues": ["<short, specific issue>", ...], '
        '"summary": "<one sentence>"}. approved=true only if you found no real defects.'
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=NOVA_AGENT_MODEL,
            # 600 was too small for a real review on a large diff -- this
            # account/model defaults to extended thinking (see this
            # function's own docstring on the ThinkingBlock gotcha), and on
            # a big/complex diff the invisible thinking block can eat the
            # whole budget before any real text gets emitted, producing a
            # response with no usable text block at all. Confirmed live
            # 2026-07-29: 5/6 real reviews on the held-out eval's diffs
            # failed this way under 600.
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": f"Task:\n{task_description}\n\nDiff:\n{diff}"}],
        )
        # Real bug found live: message.content[0] is not reliably the text
        # block -- this account/model returns a leading ThinkingBlock (no
        # usable .text) before the real TextBlock for some prompts. Find the
        # first block with type "text" explicitly rather than assuming index 0.
        text_blocks = [block.text for block in message.content if block.type == "text"]
        if not text_blocks:
            raise ValueError("No text block in Claude's response.")
        raw = text_blocks[0].strip()
        # Same markdown-fence-stripping as propose_tier() -- Claude sometimes
        # wraps the JSON in a ```json ... ``` fence despite being told not to.
        unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
        parsed = json.loads(unfenced)
        return {
            "approved": bool(parsed["approved"]),
            "issues": list(parsed.get("issues", [])),
            "summary": str(parsed.get("summary", "")),
        }
    except Exception as e:
        return {"approved": False, "issues": [f"Review itself failed to run: {e}"], "summary": ""}


def _log_coding_review(branch: str, task_description: str, diff: str, review: dict) -> None:
    """
    Append one review verdict to coding_review_log.jsonl -- the natural
    side-effect data source for a future Qwen fine-tune dataset (a real
    diff paired with Claude's real corrected/approved judgment on it),
    generated automatically as a byproduct of the review pass itself
    rather than needing separate manual curation. Same JSONL-append shape
    as record_task_outcome() below.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "branch": branch,
        "task": task_description,
        "diff": diff,
        "approved": review["approved"],
        "issues": review["issues"],
        "summary": review["summary"],
    }
    with open(CODING_REVIEW_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _log_ground_truth_gate(branch: str, task_description: str, gate_result: dict) -> None:
    """
    Append one gate result to ground_truth_gate_log.jsonl -- kept separate
    from coding_review_log.jsonl since this is a mechanical check result,
    not a judged verdict, and gives a training/monitoring signal that's
    independent of (and a useful cross-check against) Claude's own review
    pass. Same JSONL-append shape as _log_coding_review()/
    record_task_outcome().
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "branch": branch,
        "task": task_description,
        "passed": gate_result["passed"],
        "hard_fails": gate_result["hard_fails"],
        "warnings": gate_result["warnings"],
    }
    with open(GROUND_TRUTH_GATE_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Observability Phase 2 (86bb7pazm) -- additive, tags the same result
    # onto Langfuse keyed to the real failure registry. Fails open on its
    # own; never disturbs the JSONL write above.
    log_gate_result(branch, gate_result)


def record_task_outcome(branch: str, outcome: str, note: str = "") -> None:
    """
    Record whether a coding-agent branch was merged, discarded, or stopped
    itself on a budget halt. This is the missing link between raw per-turn
    telemetry (agent_log.jsonl) and a future curated training set — without
    it, there's no way to tell a clean merged outcome apart from a run that
    hit a harness bug or was simply discarded, just from agent_log.jsonl
    alone. Call "merged"/"discarded" by hand after each review decision;
    "budget_halt" is called automatically by run_coding_task() itself.
    """
    if outcome not in ("merged", "discarded", "budget_halt"):
        raise ValueError(f"outcome must be 'merged', 'discarded', or 'budget_halt', got '{outcome}'")

    os.makedirs(LOGS_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "branch": branch,
        "outcome": outcome,
        "note": note,
    }
    with open(TASK_OUTCOMES_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
