# nova_coding_dataset_curator.py
# Curates Nova's own coding-task history into training data for the coding
# sub-agent's eventual Qwen3 8B swap (Phase 3.5) -- repurposed from
# 86bara7pn's original spec (a separate StarCoder2/CodeLlama fine-tune fed
# from The Stack v2), which was superseded by the 2026-07-19 roadmap
# decision to merge all coding-model tracks into the one Qwen3 8B target
# nova_agentic_dataset_curator.py's external-dataset half already builds
# for. This file is the Nova-specific half: real task trajectories, not
# public code. Confirmed with Marvin before building (StarCoder2/CodeLlama
# vs. repurpose for Qwen3 8B) rather than assumed.
#
# Two lanes, deliberately NOT blended into one pretend-uniform dataset --
# each row is labeled with its real completeness, since they carry
# genuinely different amounts of signal:
#
#   "full_trajectory" (source_dataset="nova_interactive") -- complete
#   message sequences (user prompt, assistant text + tool_use, tool_result)
#   pulled directly from raw local Claude Code transcripts
#   (~/.claude/projects/**/*.jsonl). Rich, real reasoning + real tool
#   outputs included.
#
#   "tool_calls_only" (source_dataset="nova_native_orchestrator") --
#   nova_orchestrator.py's own API-driven loop (_log_agent_turn() in that
#   file) never logged the assistant's text/reasoning or the tool_result
#   content that came back from each call -- only tool_calls (name+input).
#   Real signal (task description -> real tool-call sequence), but
#   genuinely incomplete, not artificially padded to look complete.
#
# Quality filter, for both lanes: only tasks/branches with a REAL confirmed-
# good outcome are included -- never unreviewed or discarded work, matching
# this project's standing "a human always reviews before it counts"
# discipline (nova_orchestrator.py never merges its own work; nova_finetune_
# phi4.py only trains on already-corrected pairs). For nova_interactive,
# that's a real merged GitHub PR for the branch (86bawx7vj's own named goal
# --  "branch-based diff confirmation"). For nova_native_orchestrator,
# that's agent_task_outcomes.jsonl's own "merged" label, the same manual
# record nova_orchestrator.py's docstring already calls "the missing link
# between raw per-turn telemetry and a future curated training set."
#
# headless_dispatch entries are deliberately excluded from v1: checked
# their real join-ability against the Omen's dispatch_review_log.jsonl
# outcome log (keyed by ClickUp task_id) before assuming it would work --
# headless-converted agent_log.jsonl entries report branch="master" for
# --worktree sessions (a known, already-documented caveat in
# nova_omen_dispatch.py) and carry no task_id field at all, so there is no
# reliable join key between the two logs. Only 2 headless task_slugs exist
# total as of this writing, so excluding them costs little; fabricating an
# unreliable join would cost real data-quality trust.
#
# Usage:
#   nova-env\Scripts\python nova_coding_dataset_curator.py --curate
#   nova-env\Scripts\python nova_coding_dataset_curator.py --report
#   nova-env\Scripts\python nova_coding_dataset_curator.py --all

import argparse
import json
import os
import subprocess
from collections import defaultdict
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────
PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Minimum real assistant turns for a segment to count as a substantive task,
# not a stray branch touch (e.g. a one-off `git status` check on a branch
# that was really about something else). Chosen loosely, not tuned against
# real data yet -- revisit once volume is high enough to matter.
MIN_TRAJECTORY_TURNS = 2

LICENSE = "proprietary"  # Marvin's own IP, per 86bara7pn's own data policy note


def _resolve_main_checkout_root() -> Path:
    """
    Find the MAIN checkout's root, not wherever this script happens to be
    running from -- same reasoning and mechanism as
    nova_interactive_log_ingest.py's identically-named helper (this file is
    also meant to run from wherever a session's cwd is, which can be a
    worktree). `git rev-parse --git-common-dir` always resolves to the main
    checkout's real .git directory, even from inside a worktree.
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
AGENT_LOG_PATH = REPO_ROOT / "logs" / "agent_log.jsonl"
TASK_OUTCOMES_PATH = REPO_ROOT / "logs" / "agent_task_outcomes.jsonl"
CURATED_DIR = REPO_ROOT / "data" / "coding_training" / "curated"
CURATED_OUTPUT_PATH = CURATED_DIR / "coding_task_pairs.jsonl"

REPO_CWD_MARKER = "nova"  # see nova_interactive_log_ingest.py's identical constant for reasoning
NON_TASK_BRANCHES = {"master", "main", "HEAD"}

# nova_orchestrator.py's real API loop never sends/expects these block types
# (confirmed by reading its client.messages.create() call directly -- no
# `thinking` param, ever). "thinking" is a Claude Code interactive-session
# artifact (extended thinking mode); training on it unfiltered would teach a
# future Qwen3 replacement a message shape the real runtime never produces.
ALLOWED_ASSISTANT_BLOCK_TYPES = {"text", "tool_use"}


# ── Quality oracles ──────────────────────────────────────────────


def _merged_pr_branches() -> set[str]:
    """
    Every branch with a real merged PR against this repo -- the quality
    oracle for nova_interactive trajectories. A branch without a merged PR
    never contributes training data; unreviewed or unmerged work isn't
    confirmed good, matching this project's standing discipline that a
    human always reviews before anything counts.
    """
    result = subprocess.run(
        ["gh", "pr", "list", "--state", "merged", "--json", "headRefName", "--limit", "500"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {pr["headRefName"] for pr in json.loads(result.stdout)}


def _merged_native_branches() -> set[str]:
    """
    Branches nova_orchestrator.py's own record_task_outcome() marked
    "merged" in agent_task_outcomes.jsonl -- the native-loop equivalent of
    a merged PR, since those branches are merged by hand outside GitHub.
    """
    if not TASK_OUTCOMES_PATH.exists():
        return set()
    merged = set()
    with open(TASK_OUTCOMES_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("outcome") == "merged" and entry.get("branch"):
                merged.add(entry["branch"])
    return merged


# ── Lane 1: nova_interactive (full trajectories from raw transcripts) ────


def _clean_assistant_content(content) -> list[dict]:
    """Strip block types nova_orchestrator.py's real API loop never produces
    (see ALLOWED_ASSISTANT_BLOCK_TYPES's own comment)."""
    if not isinstance(content, list):
        return []
    return [block for block in content if block.get("type") in ALLOWED_ASSISTANT_BLOCK_TYPES]


def _clean_user_content(content):
    """
    Pass through a user message's real content -- either plain text (a
    typed prompt) or a list of tool_result blocks (a tool's real output).
    Drops anything else (e.g. attachment-shaped blocks) rather than
    guessing how to represent it.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        tool_results = [block for block in content if block.get("type") == "tool_result"]
        return tool_results if tool_results else None
    return None


def _flush_segment(trajectories: list, branch: str | None, messages: list, merged_branches: set[str]) -> None:
    """Append the current (session_id, branch) segment as one curated
    trajectory row, if it's on a confirmed-merged branch and substantive
    enough to be real signal."""
    if not branch or branch in NON_TASK_BRANCHES or branch not in merged_branches:
        return
    assistant_turns = sum(1 for m in messages if m["role"] == "assistant")
    if assistant_turns < MIN_TRAJECTORY_TURNS:
        return
    trajectories.append(
        {
            "messages": messages,
            "branch": branch,
            "source_dataset": "nova_interactive",
            "completeness": "full_trajectory",
            "license": LICENSE,
        }
    )


def extract_interactive_trajectories(merged_branches: set[str]) -> list[dict]:
    """
    Scan every local Claude Code transcript for segments on a real
    merged-PR branch, preserving full message content (real reasoning text
    and real tool outputs, not just tool-call names). A session can touch
    multiple branches (worktree switches) or multiple repos entirely, so
    segments are cut on both a branch change and a cwd leaving this repo.
    """
    trajectories: list[dict] = []
    if not PROJECTS_DIR.exists():
        return trajectories

    for transcript_path in PROJECTS_DIR.glob("*/*.jsonl"):
        current_branch = None
        current_messages: list[dict] = []

        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if d.get("isMeta"):
                    continue

                cwd = (d.get("cwd") or "").lower()
                branch = d.get("gitBranch") if REPO_CWD_MARKER in cwd else None

                if branch != current_branch:
                    _flush_segment(trajectories, current_branch, current_messages, merged_branches)
                    current_branch = branch
                    current_messages = []

                if branch is None:
                    continue

                msg_type = d.get("type")
                if msg_type not in ("user", "assistant"):
                    continue

                message = d.get("message") or {}
                role = message.get("role")
                content = message.get("content")

                if role == "assistant":
                    cleaned = _clean_assistant_content(content)
                    if cleaned:
                        current_messages.append({"role": "assistant", "content": cleaned})
                elif role == "user":
                    cleaned = _clean_user_content(content)
                    if cleaned:
                        current_messages.append({"role": "user", "content": cleaned})

        _flush_segment(trajectories, current_branch, current_messages, merged_branches)

    return trajectories


# ── Lane 2: nova_native_orchestrator (tool-calls-only, from agent_log.jsonl) ──


def extract_native_trajectories(merged_branches: set[str]) -> list[dict]:
    """
    nova_orchestrator.py's own direct API-loop turns -- one curated row per
    task (not per turn), task description as the user message and every
    real tool call across the whole task concatenated into one assistant
    message. No tool_result content exists to include (never logged), and
    no fabricated placeholder is substituted for it -- completeness is
    marked honestly rather than padded to look like lane 1's rows.

    Only entries with no "source" field are native (interactive/headless
    entries both carry a real "source" value, confirmed by reading
    _log_agent_turn()'s own dict literal, which predates that field).
    """
    trajectories: list[dict] = []
    if not AGENT_LOG_PATH.exists():
        return trajectories

    by_branch: dict[str, list[dict]] = defaultdict(list)
    with open(AGENT_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if "source" in entry:
                continue
            branch = entry.get("branch")
            if branch and branch in merged_branches:
                by_branch[branch].append(entry)

    for branch, entries in by_branch.items():
        entries.sort(key=lambda e: e["turn"])
        task_description = entries[0]["task"]
        tool_use_blocks = []
        for i, entry in enumerate(entries):  # noqa: B007
            for call in entry.get("tool_calls") or []:
                tool_use_blocks.append(
                    {
                        "type": "tool_use",
                        "id": f"{branch}-turn{entry['turn']}-{len(tool_use_blocks)}",
                        "name": call.get("name"),
                        "input": call.get("input"),
                    }
                )
        if not tool_use_blocks:
            continue
        trajectories.append(
            {
                "messages": [
                    {"role": "user", "content": task_description},
                    {"role": "assistant", "content": tool_use_blocks},
                ],
                "branch": branch,
                "source_dataset": "nova_native_orchestrator",
                "completeness": "tool_calls_only",
                "license": LICENSE,
            }
        )

    return trajectories


# ── Core ───────────────────────────────────────────────────────


def curate() -> None:
    """Run both lanes, write one curated .jsonl."""
    merged_pr = _merged_pr_branches()
    merged_native = _merged_native_branches()
    print(f"[curate] {len(merged_pr)} branch(es) with a real merged PR")
    print(f"[curate] {len(merged_native)} branch(es) marked merged in agent_task_outcomes.jsonl")

    interactive_rows = extract_interactive_trajectories(merged_pr)
    native_rows = extract_native_trajectories(merged_native)
    print(f"[curate] nova_interactive: {len(interactive_rows)} trajectory row(s)")
    print(f"[curate] nova_native_orchestrator: {len(native_rows)} trajectory row(s)")

    all_rows = interactive_rows + native_rows
    os.makedirs(CURATED_DIR, exist_ok=True)
    with open(CURATED_OUTPUT_PATH, "w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[curate] wrote {len(all_rows)} row(s) to {CURATED_OUTPUT_PATH}")


def report() -> None:
    """Print final counts per source/completeness and a couple of sample rows."""
    if not CURATED_OUTPUT_PATH.exists():
        print(f"No curated file found at {CURATED_OUTPUT_PATH} -- run --curate first.")
        return

    with open(CURATED_OUTPUT_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    print(f"Total curated rows: {len(rows)}\n")

    by_source: dict[str, int] = defaultdict(int)
    by_completeness: dict[str, int] = defaultdict(int)
    for row in rows:
        by_source[row["source_dataset"]] += 1
        by_completeness[row["completeness"]] += 1

    print("By source_dataset:")
    for source, count in sorted(by_source.items()):
        print(f"  {count:4d}  {source}")
    print("\nBy completeness:")
    for completeness, count in sorted(by_completeness.items()):
        print(f"  {count:4d}  {completeness}")

    if rows:
        print("\nSample row (branch, source_dataset, completeness, message count):")
        for row in rows[:3]:
            print(
                f"  {row['branch']}  |  {row['source_dataset']}  |  {row['completeness']}  |  {len(row['messages'])} messages"  # noqa: E501
            )


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Curate Nova's own coding-task training data (86bara7pn)")
    parser.add_argument("--curate", action="store_true", help="Scan real transcripts/logs, write curated .jsonl")
    parser.add_argument("--report", action="store_true", help="Print final counts + samples")
    parser.add_argument("--all", action="store_true", help="Run both phases in sequence")
    args = parser.parse_args()

    if args.all or args.curate:
        curate()
    if args.all or args.report:
        report()
