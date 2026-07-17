# nova_clickup_client.py
# All ClickUp API calls plus the Nova board's house rules (Nova Reference —
# Task Dependency & Status Discipline v1.0), as plain importable functions.
# No CLI concerns live here — nova_board.py is the thin CLI layer on top.
#
# Why the split: when MCP tool-calling (ClickUp task 86baf72n5) eventually
# lands, this module gets wrapped as MCP tool endpoints instead of CLI
# commands — same logic, second front door, not a rewrite.

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Resolved relative to this file's own location, not a hardcoded Windows
# path — the same recurring bug class already fixed twice elsewhere in
# this repo (nova_orchestrator.py's dotenv path, nova_api.py's GRAPH_PATH)
# silently breaks anything reading .env on Linux otherwise. Found live
# 2026-07-16 running nova_scheduled_dispatch.py on the Omen for the first
# time — this module had never run natively there before.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

# ── Constants / config ──────────────────────────────────────────

CLICKUP_API_BASE = "https://api.clickup.com/api/v2"

# The 🤖 Nova folder and its Board list — the only list this tool operates on.
NOVA_FOLDER_ID = "901410077876"
NOVA_BOARD_LIST_ID = "901417291831"

# Folder-scoped statuses added during the 2026-07-11 board-hygiene pass.
VALID_STATUSES = ["to do", "blocked", "in progress", "complete"]

# A task counts as stale "in progress" if it hasn't been touched in this many days.
STALE_DAYS_THRESHOLD = 7

# How many pages of tasks list_board_tasks() will fetch before giving up —
# a safety cap, not a real limit (the Nova board is nowhere near this size).
MAX_PAGES = 20


# ── Auth / HTTP plumbing ─────────────────────────────────────────

def _api_key() -> str:
    """
    Reads CLICKUP_API_KEY from the environment, same pattern as
    ANTHROPIC_API_KEY in nova_orchestrator.py. Raises immediately with a
    clear message if unset, rather than failing later with a confusing 401.
    """
    key = os.environ.get("CLICKUP_API_KEY")
    if not key:
        raise RuntimeError(
            "CLICKUP_API_KEY environment variable is not set. "
            "Add it to this machine's .env, next to this script "
            "(ClickUp Settings -> Apps -> API Token)."
        )
    return key


def _headers() -> dict:
    """ClickUp personal API tokens go in the Authorization header raw, no 'Bearer' prefix."""
    return {"Authorization": _api_key(), "Content-Type": "application/json"}


def _request(method: str, path: str, **kwargs) -> dict:
    """
    Thin wrapper around httpx for every ClickUp API call. Turns a failed
    request into a RuntimeError naming the endpoint and ClickUp's own error
    body, instead of letting a bare HTTPStatusError bubble up.
    """
    url = f"{CLICKUP_API_BASE}{path}"
    try:
        response = httpx.request(method, url, headers=_headers(), timeout=30, **kwargs)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(
            f"ClickUp API call {method} {path} failed ({e.response.status_code}): {e.response.text}"
        ) from e
    except httpx.RequestError as e:
        raise RuntimeError(f"ClickUp API call {method} {path} failed: {e}") from e
    return response.json() if response.content else {}


# ── Core API calls ──────────────────────────────────────────────

def get_task(task_id: str) -> dict:
    """Fetch a single task, including its status, dependencies, and last-updated timestamp."""
    return _request("GET", f"/task/{task_id}")


def list_board_tasks(include_closed: bool = False) -> list[dict]:
    """
    Fetch every task in the Nova Board list, paginating until a page comes
    back short of a full page. This is the source list for ready/audit/find.
    """
    tasks = []
    for page in range(MAX_PAGES):
        params = {"page": page, "include_closed": str(include_closed).lower()}
        data = _request("GET", f"/list/{NOVA_BOARD_LIST_ID}/task", params=params)
        page_tasks = data.get("tasks", [])
        tasks.extend(page_tasks)
        if len(page_tasks) < 100:
            break
    return tasks


def update_task(task_id: str, **fields) -> dict:
    """Generic task field update (name, status, priority, ...) — PUT /task/{id} with whatever fields are passed."""
    return _request("PUT", f"/task/{task_id}", json=fields)


def update_status(task_id: str, status: str) -> dict:
    """Set a task's status. Validates against VALID_STATUSES first so a typo fails loudly, not silently."""
    if status not in VALID_STATUSES:
        raise ValueError(f"'{status}' is not a valid status. Must be one of: {VALID_STATUSES}")
    return update_task(task_id, status=status)


def add_dependency(task_id: str, depends_on: str) -> dict:
    """Record that task_id depends on (is blocked by) depends_on."""
    return _request("POST", f"/task/{task_id}/dependency", json={"depends_on": depends_on})


def remove_dependency(task_id: str, depends_on: str) -> dict:
    """Remove a previously-recorded depends_on link between two tasks."""
    return _request("DELETE", f"/task/{task_id}/dependency", params={"depends_on": depends_on})


def create_task(name: str, description: str = "") -> dict:
    """Create a new task in the Nova Board list — used for human-blocker placeholder tasks."""
    return _request(
        "POST",
        f"/list/{NOVA_BOARD_LIST_ID}/task",
        json={"name": name, "description": description},
    )


def add_comment(task_id: str, comment_text: str) -> dict:
    """Post a comment on a task — used for automated status/outcome notifications (e.g. nova_scheduled_dispatch.py's non-clean-outcome alerts, 86baykvb7)."""
    return _request("POST", f"/task/{task_id}/comment", json={"comment_text": comment_text})


# ── House rules (Task Dependency & Status Discipline v1.0) ─────

def _own_blockers(task: dict) -> list[dict]:
    """
    Filters a task's raw `dependencies` field down to entries where this
    task is the one doing the depending (task_id == this task, not the
    reverse direction where some other task depends on this one).
    """
    task_id = task["id"]
    return [d for d in task.get("dependencies", []) if d.get("task_id") == task_id]


def get_unresolved_blockers(task_id: str) -> list[dict]:
    """
    This task's own blockers that aren't complete yet. An empty list means
    the task is unblocked right now — this is the pure readiness check,
    deliberately separate from qualifies_as_in_progress() below, which also
    factors in staleness (only relevant for a task already IN "in progress").
    """
    task = get_task(task_id)
    unresolved = []
    for dep in _own_blockers(task):
        blocker = get_task(dep["depends_on"])
        if blocker["status"]["status"] != "complete":
            unresolved.append(
                {"id": blocker["id"], "name": blocker["name"], "status": blocker["status"]["status"]}
            )
    return unresolved


def get_dependency_chain(task_id: str) -> list[dict]:
    """
    Walks this task's own blockers recursively, stopping at a blocker that's
    complete or has no further unresolved blockers of its own. Answers "why
    is this actually stuck" in one call instead of manual multi-task tracing.
    Returns a flat list of {id, name, status} in blocker order.
    """
    chain = []
    seen = set()
    frontier = [task_id]

    while frontier:
        current_id = frontier.pop(0)
        if current_id in seen:
            continue
        seen.add(current_id)

        current = get_task(current_id)
        for dep in _own_blockers(current):
            blocker_id = dep["depends_on"]
            if blocker_id in seen:
                continue
            blocker = get_task(blocker_id)
            blocker_status = blocker["status"]["status"]
            chain.append({"id": blocker_id, "name": blocker["name"], "status": blocker_status})
            if blocker_status != "complete":
                frontier.append(blocker_id)

    return chain


def qualifies_as_in_progress(task_id: str) -> tuple[bool, str]:
    """
    House rule: "in progress" means zero unresolved dependencies AND real
    activity within STALE_DAYS_THRESHOLD days. Returns (True, "") if the
    task's current status is earned, or (False, reason) naming the specific
    blocker or the staleness that breaks it.
    """
    task = get_task(task_id)

    unresolved = get_unresolved_blockers(task_id)
    if unresolved:
        blocker = unresolved[0]
        return False, f"blocked by {blocker['id']} ({blocker['name']})"

    last_updated = datetime.fromtimestamp(int(task["date_updated"]) / 1000, tz=timezone.utc)
    stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(days=STALE_DAYS_THRESHOLD)
    if last_updated < stale_cutoff:
        return False, f"no activity since {last_updated.date().isoformat()}"

    return True, ""


def find_stale_in_progress() -> list[dict]:
    """
    Board-wide scan for the weekly stale-check: every 'in progress' task
    that no longer qualifies under the house rule, with its reason.
    """
    stale = []
    for task in list_board_tasks():
        if task["status"]["status"] != "in progress":
            continue
        ok, reason = qualifies_as_in_progress(task["id"])
        if not ok:
            stale.append({"id": task["id"], "name": task["name"], "reason": reason})
    return stale


# ── Quick test ───────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    import sys

    # Task names/reasons are real ClickUp data (already contains em-dashes,
    # emoji) this module doesn't control — same guard as nova_board.py so a
    # plain Windows console (cp1252) can't crash on it.
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    test_task_id = sys.argv[1] if len(sys.argv) > 1 else "86bawbmdz"
    task = get_task(test_task_id)
    print(json.dumps({"id": task["id"], "name": task["name"], "status": task["status"]["status"]}, indent=2))
    ok, reason = qualifies_as_in_progress(test_task_id)
    print(f"qualifies_as_in_progress: {ok}" + (f" ({reason})" if reason else ""))
