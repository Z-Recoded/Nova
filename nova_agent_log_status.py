# nova_agent_log_status.py
# Cross-machine training-corpus status for Phase 3.5's Qwen3 8B swap trigger.
#
# agent_log.jsonl exists as two separate, independent files today — the
# Aero's C:/Nova/logs/agent_log.jsonl (written turn-by-turn by
# nova_orchestrator.py's interactive run_coding_task() loop) and the Omen's
# ~/nova/logs/agent_log.jsonl (written by nova_omen_dispatch.py's
# post-dispatch transcript converter, see that module's docstring). Neither
# machine has ever seen the other's file — logs/ is fully gitignored
# (confirmed), so there's no risk of git ever merging or duplicating
# entries between them. Found 2026-07-16 while checking real progress
# toward the swap trigger: the count was un-answerable without combining
# both files, which nothing did.
#
# Deliberately a read-only CLI, not a nova_state.db entity — confirmed with
# Marvin before building, choosing this over mirroring the
# usage_history/activity_profile push pattern (POST/GET routes on
# nova_api.py, auto-triggered by the SessionEnd hook). Those push small
# daily aggregates that a live dashboard genuinely reads; agent_log.jsonl is
# raw, ever-growing per-turn training data that nothing needs *live* access
# to — Marvin's actual question was "how much do we have toward 30-50
# diverse tasks," an occasional manual check, not a monitored metric. Adding
# a persisted entity + auto-push wiring for that would be exactly the
# "solve it before there's a second real consumer" pattern this project
# repeatedly avoids elsewhere (see the GPU-inference-seam and MCP-tool-
# calling scoping decisions in CLAUDE.md's change log). This script fetches
# both files fresh every run instead.

import argparse
import json
import subprocess
import sys

# Same three constants as nova_omen_dispatch.py/nova_omen_sync.py —
# duplicated rather than imported, matching this repo's existing convention
# of each script staying self-contained rather than cross-importing simple
# constants (see nova_omen_sync.py's identical redeclaration).
OMEN_HOST = "100.114.197.117"  # Tailscale IP — works whether or not the Aero is on the same LAN
OMEN_USER = "marvinroyal5"
OMEN_REPO_PATH = "/home/marvinroyal5/nova"

LOCAL_AGENT_LOG_PATH = "C:/Nova/logs/agent_log.jsonl"
SSH_TIMEOUT_SECONDS = 15

# Phase 3.5's own swap-trigger range (CLAUDE.md) — not a hard requirement,
# just what this report measures progress against.
SWAP_TRIGGER_MIN_TASKS = 30
SWAP_TRIGGER_MAX_TASKS = 50
HELD_OUT_FRACTION = 0.2


def _parse_jsonl(raw_text: str) -> list[dict]:
    """Parse a JSONL blob into a list of dicts, silently skipping blank/malformed lines."""
    entries = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def read_local_agent_log() -> list[dict]:
    """Read this machine's own logs/agent_log.jsonl. Empty list if it doesn't exist yet."""
    try:
        with open(LOCAL_AGENT_LOG_PATH, encoding="utf-8") as f:
            return _parse_jsonl(f.read())
    except FileNotFoundError:
        return []


def read_omen_agent_log() -> dict:
    """
    Fetch the Omen's logs/agent_log.jsonl over SSH — same host/user/path
    nova_omen_dispatch.py's transcript converter already uses. Returns
    {"entries": [...], "error": None} on success, or
    {"entries": [], "error": "..."} if the Omen is unreachable or the file
    doesn't exist yet. Never raises: a status check shouldn't fail outright
    just because the Omen happens to be off.
    """
    remote_path = f"{OMEN_REPO_PATH}/logs/agent_log.jsonl"
    try:
        result = subprocess.run(
            ["ssh", f"{OMEN_USER}@{OMEN_HOST}", f"cat {remote_path} 2>/dev/null || true"],
            capture_output=True,
            text=True,
            timeout=SSH_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return {"entries": [], "error": f"ssh exited {result.returncode}: {result.stderr.strip()}"}
        return {"entries": _parse_jsonl(result.stdout), "error": None}
    except subprocess.TimeoutExpired:
        return {"entries": [], "error": "SSH to the Omen timed out"}


def summarize_by_task(entries: list[dict]) -> dict:
    """
    Group a list of agent_log.jsonl entries by task_slug, returning
    {task_slug: {"turns": N, "models": {...}, "first_ts": ..., "last_ts": ...}}.

    task_slug, not branch, is the reliable per-task identifier here —
    nova_omen_dispatch.py's own converter docstring notes the Omen's
    entries report "master" for gitBranch on --worktree sessions, not the
    real worktree branch, so branch can't be trusted for grouping.
    """
    tasks: dict = {}
    for entry in entries:
        slug = entry.get("task_slug") or "(unknown)"
        bucket = tasks.setdefault(slug, {"turns": 0, "models": set(), "first_ts": None, "last_ts": None})
        bucket["turns"] += 1
        if entry.get("model"):
            bucket["models"].add(entry["model"])
        ts = entry.get("timestamp")
        if ts:
            bucket["first_ts"] = ts if bucket["first_ts"] is None else min(bucket["first_ts"], ts)
            bucket["last_ts"] = ts if bucket["last_ts"] is None else max(bucket["last_ts"], ts)
    return tasks


def get_combined_status() -> dict:
    """
    Merge the Aero's local agent_log.jsonl with the Omen's fetched-over-SSH
    copy, and report combined progress toward Phase 3.5's Qwen3 8B swap
    trigger (~30-50 diverse real task transcripts, 20% held out untrained).
    This is the actual cross-machine gap flagged 2026-07-16 — an accurate
    count needs both files, and nothing combined them before this.

    Caveat, found on first real run: this counts distinct task_slugs, not
    distinct underlying work. A task that failed/discarded and got retried
    (e.g. the resource headroom calculator, attempted 3 times before it
    landed) gets a fresh worktree and a fresh task_slug per attempt, so it
    counts multiple times here — "diverse" in the swap trigger's own
    language means distinct real tasks, which this number can overstate.
    Not fixed here (would need correlating task text/outcome across
    retries, more machinery than this status check warrants) — flagged in
    the CLI's printed report instead of silently presented as clean.
    """
    local_entries = read_local_agent_log()
    omen_result = read_omen_agent_log()

    local_tasks = summarize_by_task(local_entries)
    omen_tasks = summarize_by_task(omen_result["entries"])

    distinct_tasks = len(local_tasks) + len(omen_tasks)
    total_turns = sum(t["turns"] for t in local_tasks.values()) + sum(t["turns"] for t in omen_tasks.values())

    return {
        "aero": {
            "tasks": len(local_tasks),
            "turns": sum(t["turns"] for t in local_tasks.values()),
        },
        "omen": {
            "tasks": len(omen_tasks),
            "turns": sum(t["turns"] for t in omen_tasks.values()),
            "error": omen_result["error"],
        },
        "combined": {
            "distinct_tasks": distinct_tasks,
            "total_turns": total_turns,
            "swap_trigger_range": [SWAP_TRIGGER_MIN_TASKS, SWAP_TRIGGER_MAX_TASKS],
            "progress_pct_of_min": round(100 * distinct_tasks / SWAP_TRIGGER_MIN_TASKS, 1),
            "held_out_target": round(distinct_tasks * HELD_OUT_FRACTION, 1),
        },
    }


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Combined Aero+Omen agent_log.jsonl status, toward Phase 3.5's Qwen3 8B swap trigger."
    )
    parser.add_argument("--json", action="store_true", help="Print the raw status dict as JSON instead of a report.")
    args = parser.parse_args()

    status = get_combined_status()

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(f"Aero (interactive):  {status['aero']['tasks']} tasks, {status['aero']['turns']} turns")
        if status["omen"]["error"]:
            print(f"Omen (headless):     unreachable ({status['omen']['error']})")
        else:
            print(f"Omen (headless):     {status['omen']['tasks']} tasks, {status['omen']['turns']} turns")

        combined = status["combined"]
        print(f"\nCombined: {combined['distinct_tasks']} distinct task_slugs, {combined['total_turns']} total turns")
        print(
            f"Swap trigger target: {combined['swap_trigger_range'][0]}-{combined['swap_trigger_range'][1]} tasks "
            f"({combined['progress_pct_of_min']}% of the minimum)"
        )
        print(f"20% held-out target at current count: ~{combined['held_out_target']} tasks")
        print(
            "\nNote: this counts distinct task_slugs, not distinct underlying work — a task retried "
            "after a failed/discarded attempt gets a new worktree and a new task_slug each time, so it "
            "counts multiple times here. Cross-check against logs/agent_task_outcomes.jsonl (Aero only) "
            "or judge by task text if you need true diversity, not raw transcript count."
        )
