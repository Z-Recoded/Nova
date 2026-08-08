# nova_status_digest.py
# Writes NOVA_STATUS.md -- a structural summary of the Nova board's current
# state, so Claude Chat can read cached context instead of re-querying the
# whole board from scratch every planning session. Claude Code runs this
# after sessions that change board state (task completed, task created, a
# dependency link changed) -- not on read-only sessions. Claude Chat never
# writes to this file; it's a starting point, not ground truth -- falls back
# to querying ClickUp directly if it looks stale or contradicts a session.
#
# Reuses nova_clickup_client.py's functions directly rather than
# reimplementing board-categorization logic a second time -- nova_board.py's
# own `ready`/`audit` commands make the same underlying calls.
#
# Out of scope, per spec: not real-time, no task-content summarization
# (structure only), one-way (Chat never writes here).

import json
from datetime import UTC, datetime
from pathlib import Path

import nova_clickup_client as client

STATUS_PATH = Path(__file__).parent / "NOVA_STATUS.md"
SNAPSHOT_PATH = Path(__file__).parent / ".nova_status_snapshot.json"


def _categorize_board() -> dict:
    """
    Buckets every non-complete Nova board task by {id, name}: in_progress and
    blocked mirror the task's raw ClickUp status; ready uses the same
    zero-unresolved-dependencies check nova_board.py's own `ready` command
    uses, so a "blocked" task with no real blocker left shows up in both --
    that overlap is itself a signal worth seeing, not a bug to hide.
    """
    in_progress, blocked, ready = [], [], []
    tasks = client.list_board_tasks()
    cache = {t["id"]: t for t in tasks}
    for task in tasks:
        status = task["status"]["status"]
        if status == "complete":
            continue

        entry = {"id": task["id"], "name": task["name"]}
        if status == "in progress":
            in_progress.append(entry)
        elif status == "blocked":
            blocked.append(entry)

        if status != "in progress" and not client.get_unresolved_blockers(task["id"], task=task, cache=cache):
            ready.append(entry)

    return {"ready": ready, "in_progress": in_progress, "blocked": blocked}


def _load_previous_snapshot() -> dict | None:
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _diff_snapshots(previous: dict | None, current: dict) -> list[str]:
    """Plain-English list of which tasks moved category since the last digest."""
    if previous is None:
        return ["First digest -- no prior snapshot to compare against."]

    prev_by_id = {t["id"]: (category, t["name"]) for category, tasks in previous.items() for t in tasks}
    curr_by_id = {t["id"]: (category, t["name"]) for category, tasks in current.items() for t in tasks}

    changes = []
    for task_id, (category, name) in curr_by_id.items():
        if task_id not in prev_by_id:
            changes.append(f"NEW -> {category}: {task_id} ({name})")
        elif prev_by_id[task_id][0] != category:
            changes.append(f"{prev_by_id[task_id][0]} -> {category}: {task_id} ({name})")
    for task_id, (category, name) in prev_by_id.items():
        if task_id not in curr_by_id:
            changes.append(f"{category} -> complete/removed: {task_id} ({name})")

    return changes or ["No category changes since last digest."]


def write_digest(session_notes: list[str] | None = None) -> None:
    """
    Regenerates NOVA_STATUS.md from the board's current state. session_notes
    is an optional list of plain-English lines describing what a specific
    session actually did -- structure only, never full task descriptions.
    """
    current = _categorize_board()
    changes = _diff_snapshots(_load_previous_snapshot(), current)
    now = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Nova Board Status Digest",
        "",
        f"Last updated: {now}",
        "",
        "One-way snapshot written by Claude Code after sessions that change board",
        "state. Claude Chat reads this as a cheap starting point, not ground truth --",
        "falls back to querying ClickUp directly if it looks stale.",
        "",
    ]

    if session_notes:
        lines += ["## This session", ""] + [f"- {note}" for note in session_notes] + [""]

    lines += ["## Changed since last digest", ""] + [f"- {c}" for c in changes] + [""]

    for label, key in (("In progress", "in_progress"), ("Blocked", "blocked"), ("Ready", "ready")):
        tasks = current[key]
        lines += [f"## {label} ({len(tasks)})", ""]
        lines += [f"- {t['id']}  {t['name']}" for t in tasks] if tasks else ["- none"]
        lines += [""]

    STATUS_PATH.write_text("\n".join(lines), encoding="utf-8")
    SNAPSHOT_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")


if __name__ == "__main__":
    write_digest()
    print(f"Wrote {STATUS_PATH}")
