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
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from nova_config import is_framework_integration_enabled
from nova_skills import get_skill_version, load_skill
from nova_token_budget import get_budget_status, record_usage
from nova_tools import file_replace, list_files, read_file, run_command, write_file

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ─────────────────────────────────────────────────────
NOVA_REPO_ROOT = "C:/Nova"
NOVA_AGENT_WORKTREES_ROOT = "C:/nova-agent-worktrees"

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

LOGS_DIR = "C:/Nova/logs"
AGENT_LOG_PATH = f"{LOGS_DIR}/agent_log.jsonl"
TASK_OUTCOMES_LOG_PATH = f"{LOGS_DIR}/agent_task_outcomes.jsonl"

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
                "old_str": {"type": "string", "description": "Exact text to replace — must appear exactly once in the file."},
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
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True
    )
    if not status.stdout.strip():
        return False

    commit_message = f"{_summarize_task(task_description)}\n\nWritten by nova_orchestrator.py (Nova's coding sub-agent)."
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
    """Return the full diff of this worktree's branch against master."""
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
        "run_command can technically reach outside this worktree (e.g. `cd`), "
        "but never do that except through python/pip resolving to the live "
        "venv as just described — all file edits must go through write_file "
        "or file_replace, scoped to this worktree. For edits to a file that "
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


def _execute_tool(name: str, tool_input: dict, root: str) -> dict:
    """Dispatch one Claude tool_use call to the matching nova_tools function."""
    try:
        if name == "read_file":
            return {"content": read_file(tool_input["path"], root)}
        if name == "write_file":
            write_file(tool_input["path"], tool_input["content"], root)
            return {"content": f"Wrote {tool_input['path']}"}
        if name == "file_replace":
            file_replace(tool_input["path"], tool_input["old_str"], tool_input["new_str"], root)
            return {"content": f"Replaced content in {tool_input['path']}"}
        if name == "list_files":
            return {"content": "\n".join(list_files(tool_input["path"], root))}
        if name == "run_command":
            result = run_command(tool_input["cmd"], root)
            return {"content": json.dumps(result)}
        return {"content": f"Unknown tool '{name}'", "is_error": True}
    except Exception as e:
        return {"content": str(e), "is_error": True}


def _log_agent_turn(
    slug: str, branch: str, turn: int, task: str, response, skill_category: str | None, skill_version: str | None
) -> None:
    """Append one turn of an agent task to agent_log.jsonl (JSONL, mirrors nova_log.py)."""
    os.makedirs(LOGS_DIR, exist_ok=True)
    tool_calls = [
        {"name": block.name, "input": block.input}
        for block in response.content
        if block.type == "tool_use"
    ]
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
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Export it before calling run_coding_task()."
        )
    client = anthropic.Anthropic(api_key=api_key)
    budget_gate_enabled = is_framework_integration_enabled("token_budget_governor")
    skill_injection_enabled = is_framework_integration_enabled("skill_injection")

    slug = _slugify(task_description)
    worktree_path, branch_name = _create_worktree(slug)
    root = str(worktree_path)

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

    if is_framework_integration_enabled("langgraph_orchestration"):
        # Lazy import: langgraph is only ever imported when this flag is on,
        # so a missing/broken install can't affect Nova while the feature is
        # disabled (the default). See nova_orchestrator_graph.py for the
        # graph itself — same turn-by-turn behavior as the inline loop below,
        # just expressed as LangGraph nodes/edges instead.
        from nova_orchestrator_graph import run_via_langgraph
        final_status, turns_used = run_via_langgraph(
            client, system_prompt, messages, root, slug, branch_name, task_description,
            skill_category, skill_version, budget_gate_enabled,
            NOVA_AGENT_MAX_TURNS, NOVA_AGENT_MODEL, NOVA_AGENT_MAX_TOKENS,
            _log_agent_turn, _execute_tool,
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
                result = _execute_tool(block.name, block.input, root)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result["content"],
                    "is_error": result.get("is_error", False),
                })
            messages.append({"role": "user", "content": tool_results})
        else:
            final_status = "max_turns_reached"

    elapsed_s = round(time.time() - started_at, 1)
    diff = _git_diff_against_master(root)
    budget_status = get_budget_status() if budget_gate_enabled else {"enabled": False}

    commit_note = ""
    if final_status == "stopped_budget_halt":
        commit_note = (
            f"[budget-halt] stopped at {budget_status.get('session_pct')}% "
            f"session budget, task left {final_status}"
        )
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
        "budget_status": budget_status,
        "next_steps": (
            f"Review: git diff master...{branch_name} (from C:/Nova). "
            f"Merge when satisfied: git merge {branch_name}. "
            f"Then remove the worktree: git worktree remove {root}"
        ) if committed else "Nothing was changed — no commit made, nothing to merge.",
    }


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
