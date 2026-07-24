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
#
# Real sandboxing wired in 2026-07-23 (86baf72qq/86barex1u groundwork):
# gated behind nova_config.json's scheduled_dispatch.sandboxed_dispatch_enabled
# (default off), run_scheduled_dispatch() below can route through
# nova_omen_dispatch.dispatch_headless_task_sandboxed() instead — real
# Docker containment for the exact path this file's own docstring above
# flags as the highest-stakes one to get right (fully unattended, "proven
# behavior matters most"). Not the same category of risk the "don't grow a
# second untested code path" warning above was written against: that
# warned against a LESS-tested shortcut (skip SSH); this is a MORE-tested,
# additive replacement (dispatch_headless_task_sandboxed() was verified
# live twice, including a real negative-containment check, before this
# wiring existed at all) — default off until it's also been proven under
# real cron-firing conditions, not just manual invocation.

import json
import os
from datetime import datetime
from pathlib import Path

import httpx

from nova_clickup_client import add_comment, add_tag, get_task, update_status
from nova_config import get_max_unreviewed_dispatches, is_review_backpressure_enabled, is_sandboxed_dispatch_enabled
from nova_escalation import NOVA_API_URL, is_dispatch_paused
from nova_omen_dispatch import dispatch_headless_task, dispatch_headless_task_sandboxed
from nova_task_queue import (
    detect_tier_candidates,
    get_practice_queue_tasks,
    persist_tier_watermarks,
    register_tier_proposal,
    resolve_task_description,
)

LOCK_PATH = Path(__file__).resolve().parent / ".scheduled_dispatch.lock"
LOG_PATH = Path(__file__).resolve().parent / "logs" / "scheduled_dispatch_log.jsonl"
# Separate from LOG_PATH (the raw per-firing outcome log, written
# automatically) — this one is only ever written by a human calling
# record_dispatch_review() by hand, mirroring nova_orchestrator.py's
# record_task_outcome()/agent_task_outcomes.jsonl split for the interactive
# path (86bawpvzz implication #2).
REVIEW_LOG_PATH = Path(__file__).resolve().parent / "logs" / "dispatch_review_log.jsonl"

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
    """Append one JSONL line — no rotation here, same accepted-and-deferred scope as 86barby7t at this log's much lower volume (<=12 entries/day)."""  # noqa: E501
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file into a list of dicts, silently skipping blank/malformed lines. Empty list if the file doesn't exist yet."""  # noqa: E501
    if not path.exists():
        return []
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def record_dispatch_review(task_id: str, outcome: str, note: str = "") -> None:
    """
    Record a human review decision for one headless-dispatched ClickUp
    task (86bawpvzz implication #2). Mirrors nova_orchestrator.py's
    record_task_outcome() hand-call discipline, but keyed by task_id
    rather than git branch — scheduled_dispatch_log.jsonl entries don't
    carry a branch name (dispatch_headless_task() uses Claude Code's own
    --worktree flag, which manages its own branch internally). Call this
    by hand after reviewing a dispatched task's actual diff/outcome.
    """
    if outcome not in ("merged", "discarded"):
        raise ValueError(f"outcome must be 'merged' or 'discarded', got '{outcome}'")
    REVIEW_LOG_PATH.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "task_id": task_id,
        "outcome": outcome,
        "note": note,
    }
    with open(REVIEW_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _is_clean_outcome(result: dict) -> bool:
    """
    True only if a real dispatch happened and finished the way a normal
    task should: session_id present (a genuine round-trip happened) and
    success True (claude -p's own result JSON reported stop_reason
    "end_turn", not is_error). Everything else — infra failure (no
    session_id at all: SSH/timeout/no-result-JSON), a non-end_turn stop,
    or an error before dispatch even started (task resolution failure) —
    is "non-clean" and triggers a ClickUp comment (86bawpvzz implication
    #6, Layer 1).
    """
    return result.get("session_id") is not None and bool(result.get("success"))


def _post_non_clean_comment(task_id: str, task_name: str, result: dict, phase: str) -> None:
    """
    Post a ClickUp comment describing a non-clean dispatch outcome, so
    Marvin finds out without reading scheduled_dispatch_log.jsonl on the
    Omen by hand. Best-effort: a comment-posting failure must not take
    down a dispatch that already happened — same defensive pattern as the
    existing ClickUp status-transition try/except in run_scheduled_dispatch().
    Only ever reached for a genuinely non-clean, non-escalation outcome —
    handle_dispatch_outcome() checks escalation first (86bax0wkj) and
    routes those to _handle_escalation() instead — so this still only
    reports what the result dict already says, it does not invent
    stuck-run detection. ClickUp is the only notification channel that
    works today (confirmed 2026-07-16: Open WebUI push doesn't exist,
    Slack/email need credentials Nova lacks) — Layer 2 (a dashboard tile)
    and Layer 3 (real push, Langfuse) are deferred, per 86baykvb7's own
    layered design.
    """
    lines = [f"**Autonomous dispatch — non-clean outcome ({phase}): {task_name}**", ""]
    if result.get("error"):
        lines.append(f"- error: {result['error']}")
    if result.get("stop_reason"):
        lines.append(f"- stop_reason: {result['stop_reason']}")
    if "session_id" in result:
        lines.append(f"- session_id: {result.get('session_id')}")
    if result.get("fuel_source"):
        lines.append(f"- fuel_source: {result['fuel_source']}")
    if result.get("cost_usd") is not None:
        lines.append(f"- cost_usd: {result['cost_usd']}")
    if result.get("summary"):
        lines.append(f"- summary: {result['summary']}")
    lines.append("")
    lines.append("Posted automatically by nova_scheduled_dispatch.py (86baykvb7 Layer 1).")

    try:
        add_comment(task_id, "\n".join(lines))
    except Exception as e:
        print(f"Failed to post non-clean-outcome comment on {task_id}: {e}")


def _pending_escalation_task_ids() -> set:
    """
    task_ids currently mid-escalation (status "pending" or "resuming" in
    nova_api.py's system/pending_escalations) — excluded from
    count_unreviewed_dispatches() below, since a task awaiting Marvin's
    answer isn't "done and unreviewed," it's "not done yet" (86bax0wkj).

    Fails toward an EMPTY set on any error (unreachable API, bad JSON) —
    i.e. toward NOT excluding anything, keeping the backpressure cap
    conservative (may still block a new dispatch it didn't strictly need
    to) rather than under-counting and letting more autonomous dispatches
    through than max_unreviewed_dispatches intends. Same fail-toward-the-
    restrictive-case instinct as is_dispatch_paused()'s fail-toward-paused.
    """
    try:
        response = httpx.get(f"{NOVA_API_URL}/escalations", timeout=10)
        escalations = response.json()
    except Exception:
        return set()
    return {
        entry["task_id"]
        for entry in escalations.values()
        if isinstance(entry, dict) and entry.get("status") in ("pending", "resuming")
    }


def count_unreviewed_dispatches() -> int:
    """
    Unreviewed = a scheduled_dispatch_log.jsonl entry with a real
    session_id (a genuine round-trip happened, whether the task succeeded
    or reported a real blocker) whose task_id has no matching entry yet in
    dispatch_review_log.jsonl, AND isn't currently sitting mid-escalation
    awaiting Marvin's answer (86bax0wkj — see
    _pending_escalation_task_ids()). Deliberately the same "session_id
    present" definition run_scheduled_dispatch() already uses for its own
    ClickUp status transition, not "success" — a completed-but-blocked run
    still needs a human review decision, not just a failed one.
    """
    dispatched_task_ids = {e["task_id"] for e in _read_jsonl(LOG_PATH) if e.get("session_id") is not None}
    reviewed_task_ids = {e["task_id"] for e in _read_jsonl(REVIEW_LOG_PATH)}
    pending_escalation_task_ids = _pending_escalation_task_ids()
    return len(dispatched_task_ids - reviewed_task_ids - pending_escalation_task_ids)


def _handle_escalation(task_id: str, task_name: str, result: dict, phase: str) -> None:
    """
    Register a paused-for-escalation dispatch outcome (86bax0wkj) —
    registers it with nova_api.py's /escalations (never a direct
    nova_state.py import: nova_state.py's DB_PATH is a hardcoded Windows
    path, the same cross-machine bug class that already broke
    dispatch_pause when read directly on the Omen — see
    nova_escalation.py's own header comment), tags the ClickUp task
    awaiting-answer, and posts a comment with the question so it's visible
    without opening /escalations-ui. All three best-effort (never raise
    past this function) — a comment/tag/registration failure can't take
    down a dispatch that already happened, same posture as
    _post_non_clean_comment().
    """
    escalation = result.get("escalation") or {}
    payload = {
        "task_id": task_id,
        "task_name": task_name,
        "session_id": result.get("session_id"),
        "worktree_path": result.get("worktree_path"),
        "worktree_name": result.get("worktree_name"),
        "question": escalation.get("question"),
        "options_considered": escalation.get("options_considered", []),
        "context": escalation.get("context"),
        "fuel_source": result.get("fuel_source"),
        "phase": phase,
        "malformed": escalation.get("malformed", False),
    }
    try:
        httpx.post(f"{NOVA_API_URL}/escalations", json=payload, timeout=10)
    except Exception as e:
        print(f"Failed to register escalation for {task_id}: {e}")

    try:
        add_tag(task_id, "awaiting-answer")
    except Exception as e:
        print(f"Failed to tag {task_id} awaiting-answer: {e}")

    lines = [f"**Awaiting your answer — {task_name}**", ""]
    if escalation.get("malformed"):
        lines.append("_Escalation block was malformed — question may be incomplete. Check the raw summary below._")
    lines.append(f"- question: {escalation.get('question') or '(none parsed)'}")
    if escalation.get("options_considered"):
        lines.append("- options: " + "; ".join(escalation["options_considered"]))
    if escalation.get("context"):
        lines.append(f"- context: {escalation['context']}")
    lines.append("")
    lines.append("Answer at /escalations-ui.")
    try:
        add_comment(task_id, "\n".join(lines))
    except Exception as e:
        print(f"Failed to post escalation comment on {task_id}: {e}")


def handle_dispatch_outcome(task_id: str, task_name: str, result: dict, phase: str) -> dict:
    """
    Shared tail for both a fresh dispatch (phase="dispatch", called from
    run_scheduled_dispatch() below) and a resumed escalation
    (phase="resume", called later from nova_api.py's background task once
    Marvin answers via /escalations-ui — not from run_scheduled_dispatch()
    itself, since a resume doesn't go through the pick/lock/resolve steps).

    Ordering matters and is deliberately if/elif, mutually exclusive: the
    escalation branch is checked FIRST. A result that paused for an
    escalation has a real session_id but success is not True (the run
    didn't finish) — without checking escalation first, it would also
    trip _is_clean_outcome() == False and fire _post_non_clean_comment()
    as if it were a real failure, double-commenting alongside whatever
    _handle_escalation() itself posts. It never falls through to both.
    """
    if result.get("session_id") is not None:
        try:
            get_task(task_id)  # confirm it still exists before writing
            update_status(task_id, "in progress")
        except Exception as e:
            result["status_update_error"] = str(e)

    outcome = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "task_id": task_id,
        "task_name": task_name,
        "fuel_source": result.get("fuel_source"),
        "success": result.get("success"),
        "cost_usd": result.get("cost_usd"),
        "session_id": result.get("session_id"),
        "summary": result.get("summary"),
        "phase": phase,
    }
    _log_outcome(outcome)

    escalation = result.get("escalation") or {}
    if escalation.get("escalation_needed"):
        _handle_escalation(task_id, task_name, result, phase)
    elif not _is_clean_outcome(result):
        _post_non_clean_comment(task_id, task_name, result, phase=phase)

    return {"status": "dispatched", **outcome}


def _register_tier_proposals() -> int:
    """
    Task tiering (86bb01wur) — calls nova_task_queue.detect_tier_candidates()
    (the polling-based new/rescoped-task diff, a pure read with no side
    effects) and runs nova_task_queue.register_tier_proposal() for each
    candidate — the exact same propose->register->tag->comment pipeline
    the --sweep-tiers CLI backfill uses, not a duplicate. All best-effort
    per candidate — one failed proposal must not block the rest, and must
    not block this firing's actual dispatch work below.

    Watermarks are persisted only AFTER every candidate has actually been
    attempted (persist_tier_watermarks(), not a side effect of the diff
    itself) — a real bug found live during verification: the first
    version persisted watermarks unconditionally inside
    detect_tier_candidates(), so an inspection-only call could silently
    mark the backlog "seen" without a real proposal ever having been
    attempted for it.

    Deliberately runs regardless of the dispatch-pause switch, unlike the
    dispatch flow it precedes: proposing a tier is Nova doing its own
    triage bookkeeping (a lightweight completion call, no worktree, no
    SSH dispatch), not starting an autonomous coding run — the same class
    of distinction already drawn for resuming an escalation.

    Returns the number of proposals successfully registered, for the
    firing's own summary dict.
    """
    result = detect_tier_candidates()
    registered = sum(
        register_tier_proposal(candidate["task"], candidate["trigger"]) for candidate in result["candidates"]
    )
    persist_tier_watermarks(result["watermarks"])
    return registered


def run_scheduled_dispatch() -> dict:
    """
    One cron-firing's worth of work: tier-proposal registration, pause
    check, lock, pick, dispatch, status transition, log. Returns a summary
    dict for CLI printing. Never raises — every failure path is caught,
    logged, and returns a dict describing what happened, so a cron-
    redirected log always has a real line per firing rather than a bare
    traceback.
    """
    tier_proposals_registered = _register_tier_proposals()

    pause_state = is_dispatch_paused()
    if pause_state["paused"]:
        return {
            "status": "paused",
            "reason": pause_state.get("reason"),
            "tier_proposals_registered": tier_proposals_registered,
        }  # noqa: E501

    if is_review_backpressure_enabled():
        max_unreviewed = get_max_unreviewed_dispatches()
        unreviewed = count_unreviewed_dispatches()
        if unreviewed >= max_unreviewed:
            return {
                "status": "review_backlog_full",
                "unreviewed": unreviewed,
                "max_unreviewed_dispatches": max_unreviewed,
                "tier_proposals_registered": tier_proposals_registered,
            }

    if not _acquire_lock():
        return {
            "status": "skipped",
            "reason": "a dispatch is already in progress (lock held by a live process)",
            "tier_proposals_registered": tier_proposals_registered,
        }

    try:
        candidates = get_practice_queue_tasks()
        if not candidates:
            return {"status": "no_candidates", "tier_proposals_registered": tier_proposals_registered}

        task = _pick_task(candidates)
        task_id = task["id"]

        try:
            resolved = resolve_task_description(task_id)
        except Exception as e:
            error_result = {"status": "error", "phase": "resolve", "task_id": task_id, "error": str(e)}
            _post_non_clean_comment(task_id, task["name"], error_result, phase="resolve")
            error_result["tier_proposals_registered"] = tier_proposals_registered
            return error_result

        # Real Docker containment when enabled -- see this module's own
        # header comment for why this is gated rather than a hard swap.
        # dispatch_headless_task_sandboxed()'s return shape matches
        # dispatch_headless_task()'s exactly (same fields
        # handle_dispatch_outcome()/_handle_escalation()/
        # _post_non_clean_comment() below all read), confirmed by direct
        # comparison before wiring this in -- no adapter needed either way.
        if is_sandboxed_dispatch_enabled():
            result = dispatch_headless_task_sandboxed(resolved["prompt"])
        else:
            result = dispatch_headless_task(resolved["prompt"])

        # Status transition, logging, and escalation/non-clean handling are
        # all shared with the resume-completion path (triggered later from
        # nova_api.py's background task) via handle_dispatch_outcome() —
        # see that function's own docstring for why the ordering there
        # matters (86bax0wkj).
        outcome = handle_dispatch_outcome(task_id, task["name"], result, phase="dispatch")
        outcome["tier_proposals_registered"] = tier_proposals_registered
        return outcome
    finally:
        _release_lock()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Cron-fired scheduled dispatch entry point, plus manual review-tracking commands."
    )
    parser.add_argument(
        "--review",
        nargs=2,
        metavar=("TASK_ID", "OUTCOME"),
        help="Record a review decision for a dispatched task, e.g. --review 86bayjdrh merged",
    )
    parser.add_argument("--note", default="", help="Optional note to attach to --review.")
    parser.add_argument(
        "--unreviewed-count", action="store_true", help="Print the current unreviewed-dispatch count and exit."
    )  # noqa: E501
    args = parser.parse_args()

    if args.review:
        review_task_id, review_outcome = args.review
        record_dispatch_review(review_task_id, review_outcome, args.note)
        print(f"Recorded '{review_outcome}' for {review_task_id}.")
    elif args.unreviewed_count:
        print(json.dumps({"unreviewed": count_unreviewed_dispatches()}, indent=2))
    else:
        print(json.dumps(run_scheduled_dispatch(), indent=2))
