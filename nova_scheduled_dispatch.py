# nova_scheduled_dispatch.py
# The actual cron-fired entry point for 86bax0exx's orchestration layer —
# steps 3-4 (invocation -> monitoring, in the loose sense of "did it
# happen, record what happened"). Runs on the Omen only, via a user
# crontab entry (every 2 hours, confirmed with Marvin 2026-07-16) — the
# Omen is the one always-on machine, and neither of Claude Code's own
# scheduling tools fit: CronCreate is session-only (dies with the
# conversation that created it), and RemoteTrigger/the schedule skill
# bills through the metered Messages API, never touching the Omen's own
# `claude -p` subscription login — either would silently defeat the whole
# dual-fuel design in nova_omen_dispatch.py.
#
# Picks one task per firing from nova_task_queue.get_practice_queue_tasks()
# (the curated, hand-tagged "autonomy-safe" subset — deliberately NOT the
# full backlog; 86bawpvzz already flagged full auto-selection as its own
# unresolved trust-boundary question) and dispatches it via the existing,
# already-verified nova_omen_dispatch.dispatch_headless_task(). Reuses
# that function's SSH-wrapped mechanism unchanged (the Omen SSHes to
# itself over a new, dedicated keypair — see CLAUDE.md) rather than
# growing a "local, skip SSH" branch on it: that would be a second,
# untested code path for exactly the unattended case where proven
# behavior matters most.
#
# Prerequisite fix that shipped alongside this (2026-07-16): the pause
# switch (nova_escalation.is_dispatch_paused()) used to read a local
# nova_state.db import that silently pointed at a disconnected file when
# checked from the Omen. Without that fix, this script would have been
# the first thing to expose it — a pause Marvin sets from the Aero would
# never have been seen here.

import json
import os
from datetime import datetime
from pathlib import Path

from nova_clickup_client import get_task, update_status
from nova_escalation import is_dispatch_paused
from nova_omen_dispatch import dispatch_headless_task
from nova_task_queue import get_practice_queue_tasks, resolve_task_description

LOCK_PATH = Path(__file__).resolve().parent / ".scheduled_dispatch.lock"
LOG_PATH = Path(__file__).resolve().parent / "logs" / "scheduled_dispatch_log.jsonl"

# Stable-sort key order — unlisted/None priority sorts last, not first.
PRIORITY_ORDER = ["urgent", "high", "normal", "low"]


def _acquire_lock() -> bool:
    """
    Atomic create-if-absent lock (O_EXCL, not check-then-write — closes a
    real TOCTOU race a naive version would have) so a slow-running
    dispatch (up to nova_omen_dispatch.DISPATCH_TIMEOUT_SECONDS, 30 min)
    can't overlap with the next 2-hourly cron firing. On a collision,
    checks whether the PID that holds the lock is still alive; if it's
    dead, breaks the stale lock and retries the exclusive create once.
    Returns False if a live lock is genuinely held.
    """
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        pass

    try:
        held_pid = int(LOCK_PATH.read_text().strip())
        os.kill(held_pid, 0)  # raises if the process is gone
        return False  # still alive — a real overlap, don't proceed
    except (ValueError, ProcessLookupError, OSError):
        pass  # unreadable, malformed, or dead — treat as stale

    LOCK_PATH.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False  # lost the retry race to another process


def _release_lock() -> None:
    LOCK_PATH.unlink(missing_ok=True)


def _pick_task(tasks: list[dict]) -> dict:
    """
    Highest priority first (PRIORITY_ORDER), unlisted/None priority last;
    board-listing order as a tiebreak (not a strong ordering guarantee,
    just whatever ClickUp's API happened to return that call).
    """
    def sort_key(task: dict) -> int:
        priority = task.get("priority")
        return PRIORITY_ORDER.index(priority) if priority in PRIORITY_ORDER else len(PRIORITY_ORDER)

    return sorted(tasks, key=sort_key)[0]


def _log_outcome(entry: dict) -> None:
    """Append one JSONL line — no rotation here, same accepted-and-deferred scope as 86barby7t at this log's much lower volume (<=12 entries/day)."""
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def run_scheduled_dispatch() -> dict:
    """
    One cron-firing's worth of work: pause check, lock, pick, dispatch,
    status transition, log. Returns a summary dict for CLI printing.
    Never raises — every failure path is caught, logged, and returns a
    dict describing what happened, so a cron-redirected log always has a
    real line per firing rather than a bare traceback.
    """
    pause_state = is_dispatch_paused()
    if pause_state["paused"]:
        return {"status": "paused", "reason": pause_state.get("reason")}

    if not _acquire_lock():
        return {"status": "skipped", "reason": "a dispatch is already in progress (lock held by a live process)"}

    try:
        candidates = get_practice_queue_tasks()
        if not candidates:
            return {"status": "no_candidates"}

        task = _pick_task(candidates)
        task_id = task["id"]

        try:
            resolved = resolve_task_description(task_id)
        except Exception as e:
            return {"status": "error", "phase": "resolve", "task_id": task_id, "error": str(e)}

        result = dispatch_headless_task(resolved["prompt"])

        # Only transition status when a real round-trip actually happened
        # (session_id present) — not on "success", which would leave a
        # genuinely-blocked-but-completed run silently re-picked forever,
        # and not on an infra failure (no session_id), which should
        # naturally retry next cycle rather than get stuck in limbo.
        if result.get("session_id") is not None:
            try:
                get_task(task_id)  # confirm it still exists before writing
                update_status(task_id, "in progress")
            except Exception as e:
                result["status_update_error"] = str(e)

        outcome = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "task_id": task_id,
            "task_name": task["name"],
            "fuel_source": result.get("fuel_source"),
            "success": result.get("success"),
            "cost_usd": result.get("cost_usd"),
            "session_id": result.get("session_id"),
            "summary": result.get("summary"),
        }
        _log_outcome(outcome)
        return {"status": "dispatched", **outcome}
    finally:
        _release_lock()


if __name__ == "__main__":
    print(json.dumps(run_scheduled_dispatch(), indent=2))
