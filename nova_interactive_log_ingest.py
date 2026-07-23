# nova_interactive_log_ingest.py
# Captures real coding-task turns from interactive Claude Code sessions
# (this one included) into logs/agent_log.jsonl, so they count toward
# Phase 3.5's Qwen3 8B swap-trigger corpus alongside nova_orchestrator.py's
# own task loop and nova_omen_dispatch.py's headless-dispatch converter.
#
# Why this exists: nova_agent_log_status.py's own count (2026-07-22) showed
# the richest recent coding work -- a hardcoded-path-fix PR and a security-
# hygiene-tooling PR, both done this session -- happening through direct
# interactive Claude Code, not nova_orchestrator.py's separate API-driven
# loop. None of it was reaching the training corpus the swap trigger
# depends on.
#
# Generalizes nova_omen_dispatch.py's already-proven headless-dispatch
# converter (verified live: 36/36 turns matched a real transcript exactly,
# see that file's own docstring) from "one known session_id, handed a task
# description up front" to "every local transcript, segmented by git branch
# instead of by slug." Two real differences forced a different shape, not a
# copy-paste:
#
#   - `slug` stays fixed for a session's entire lifetime -- confirmed live
#     against this very conversation's own transcript, which still showed
#     the plan-mode slug from hours earlier, unchanged through several
#     unrelated worktree switches since. It can't mark task boundaries the
#     way it does for a one-shot `claude -p --worktree` dispatch, where one
#     CLI invocation is one task by construction.
#   - `gitBranch` DOES change per real distinct task, since this project's
#     own convention always puts real coding work on a feature
#     branch/worktree (see CLAUDE.md Section 8/"Git Safety Protocol") while
#     board triage, research, and planning happen on master. So this scans
#     gitBranch instead of slug, and treats anything on master/main as
#     non-task turns -- a real, already-established boundary in how this
#     project works, not a new heuristic invented for this script.
#   - No task description exists up front for an interactive segment. The
#     branch name is the label -- always available, no dependency on the
#     worktree still existing or the branch having real commits by
#     ingestion time.
#
# Scoped to sessions whose cwd is inside this repo (or one of its
# worktrees) -- nova_usage_logger.py deliberately scans ALL local Claude
# Code projects (usage draws from one account-wide subscription pool), but
# this file is specifically Nova's own coding-training corpus, not a place
# for transcripts from unrelated projects Marvin uses Claude Code for.
#
# Idempotent via a per-session turn cursor
# (logs/interactive_log_ingest_cursor.json), the same mechanism
# nova_omen_dispatch.py's headless-dispatch converter already uses --
# re-running only appends genuinely-new turns. logs/ is git-ignored, so
# none of this ever leaves the local machine via git.
#
# Usage:
#   python nova_interactive_log_ingest.py   # scan + ingest, print a summary

import json
import os
import subprocess
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _resolve_main_checkout_root() -> Path:
    """
    Find the MAIN checkout's root, not wherever this script happens to be
    running from. Unlike nova_orchestrator.py (always launched from the
    main checkout by nova_api.py), this script is meant to run from a
    SessionEnd hook that fires wherever a session's cwd currently is --
    which can be a worktree. logs/ is gitignored and local to each
    worktree's own working directory, so naively using this file's own
    location (like every other hardcoded-path fix in this project, 86bb1pkpb)
    would scatter agent_log.jsonl across every worktree that ever runs this,
    instead of the one canonical file nova_agent_log_status.py actually reads.

    `git rev-parse --git-common-dir` always resolves to the main checkout's
    real .git directory, even from inside a worktree (confirmed live) --
    its parent is the main checkout root.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip()).parent


REPO_ROOT = _resolve_main_checkout_root()
LOGS_DIR = REPO_ROOT / "logs"
AGENT_LOG_PATH = LOGS_DIR / "agent_log.jsonl"
CURSOR_PATH = LOGS_DIR / "interactive_log_ingest_cursor.json"

# Turns on these branches are never real task work. master/main is board
# triage, planning, digest refreshes, and everything else non-task-shaped
# (see module docstring). "HEAD" is not a real branch at all -- it's git's
# detached-HEAD state during operations like `git rebase`, confirmed live:
# a real rebase this session produced 152 turns labeled "HEAD", which would
# have wrongly bucketed unrelated turns from a transient git-plumbing state
# under one meaningless task_slug.
NON_TASK_BRANCHES = {"master", "main", "HEAD"}

# Repo-scoping signal for a transcript line's cwd. Deliberately a cheap
# substring check, not an exact match against REPO_ROOT -- REPO_ROOT is
# wherever THIS script happens to be running from (the main checkout, or
# whichever worktree the SessionEnd hook's cwd was at the moment it fired),
# but a single session can span multiple cwds (main checkout, one or more
# worktrees, the sibling nova-agent-worktrees/ dir outside the repo
# entirely per CLAUDE.md). An exact-prefix match against REPO_ROOT would
# silently miss turns from any cwd other than the one the script happened
# to launch from. Every real Nova-related directory in this project's own
# established naming convention contains "nova" (C:/Nova, its worktrees,
# C:/nova-agent-worktrees) -- good enough for v1 without needing to
# enumerate every possible worktree location.
REPO_CWD_MARKER = "nova"


def _load_cursor() -> dict:
    """Per-session turn cursor: {session_id: last_ingested_turn}."""
    try:
        with open(CURSOR_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cursor(cursor: dict) -> None:
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(CURSOR_PATH, "w", encoding="utf-8") as f:
        json.dump(cursor, f)


def find_transcript_files() -> list[Path]:
    """Every local Claude Code transcript, across all projects -- filtered
    to this repo's own sessions inside ingest_transcript() via each line's
    own cwd field, not here, since a session's cwd can change mid-file
    (e.g. an EnterWorktree call) and the project-directory name alone
    isn't a reliable signal of that."""
    if not PROJECTS_DIR.exists():
        return []
    return list(PROJECTS_DIR.glob("*/*.jsonl"))


def ingest_transcript(transcript_path: Path, cursor: dict) -> int:
    """
    Convert one transcript's real-task turns (assistant messages whose cwd
    is inside this repo and whose gitBranch isn't in NON_TASK_BRANCHES)
    into agent_log.jsonl entries, picking up from this session's cursor
    position. Returns the number of new entries appended.
    """
    session_id = transcript_path.stem
    already_ingested = cursor.get(session_id, 0)

    new_entries = []
    turn = 0
    touched_repo = False

    with open(transcript_path, encoding="utf-8") as f:
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

            cwd = (d.get("cwd") or "").lower()
            if REPO_CWD_MARKER not in cwd:
                continue
            touched_repo = True

            turn += 1
            if turn <= already_ingested:
                continue

            branch = d.get("gitBranch")
            if not branch or branch in NON_TASK_BRANCHES:
                continue

            message = d.get("message") or {}
            usage = message.get("usage") or {}
            tool_calls = [
                {"name": block.get("name"), "input": block.get("input")}
                for block in (message.get("content") or [])
                if block.get("type") == "tool_use"
            ]
            new_entries.append(
                {
                    "timestamp": d.get("timestamp"),
                    "task_slug": branch,
                    "branch": branch,
                    "turn": turn,
                    "task": branch,
                    "skill_category": None,
                    "skill_version": None,
                    "stop_reason": message.get("stop_reason"),
                    "tool_calls": tool_calls,
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                    "model": message.get("model"),
                    "source": "interactive",
                    "session_id": session_id,
                }
            )

    if not touched_repo:
        return 0

    if new_entries:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(AGENT_LOG_PATH, "a", encoding="utf-8") as f:
            for entry in new_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    cursor[session_id] = turn
    return len(new_entries)


def run() -> dict:
    """Scan every local transcript, ingest any repo-scoped, real-task-branch
    turns not already captured. Returns a summary dict."""
    cursor = _load_cursor()
    total_converted = 0
    sessions_touched = 0

    for transcript_path in find_transcript_files():
        converted = ingest_transcript(transcript_path, cursor)
        if converted:
            total_converted += converted
            sessions_touched += 1

    _save_cursor(cursor)
    return {"converted": total_converted, "sessions_touched": sessions_touched}


if __name__ == "__main__":
    print(json.dumps(run()))
