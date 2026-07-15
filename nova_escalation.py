# nova_escalation.py
# Escalation-hook stub + pause-at-will switch for headless coding-agent
# dispatch — step 5 of 86bax0exx's orchestration checklist (readiness
# detection -> task resolution -> invocation -> monitoring -> escalation
# hook -> failure/rollback).
#
# Deliberately its own module rather than living inside
# nova_omen_dispatch.py, mirroring why nova_token_budget.py isn't folded
# into nova_orchestrator.py: nova_orchestrator.py's own worktree loop is a
# plausible second caller later, not just the Omen dispatch path.
#
# check_escalation() is a stub only — always reports "no escalation
# needed." It takes the generic result dict dispatch_headless_task()
# already returns (success/session_id/summary/stop_reason), not raw
# Claude Code CLI session internals, per 86bax0exx's requirement that the
# escalation interface stay backend-agnostic (Claude Code CLI today,
# OpenHands+local-model later). Real detection logic — and the
# pause/package/notify/wait/resume flow this feeds — is 86bax0wkj (Nova
# Controller v1), not yet scoped.
#
# is_dispatch_paused()/set_dispatch_pause() are real, not stubbed: Marvin
# asked directly (2026-07-14, captured on 86bax0exx) for the ability to
# pause the headless runner at will, so no headless session runs while
# he's actively building interactively. State persists to nova_state.db
# (system/dispatch_pause) rather than a local JSON file — nova_state.db is
# the "current reality" layer a future Controller UI would read/write
# anyway, so a pause flag belongs there, not in a one-off file.

from datetime import datetime
from typing import Optional

from nova_state import get_state, write_state


# ── Pause-at-will ────────────────────────────────────────────────

def is_dispatch_paused() -> dict:
    """
    Current pause state for headless dispatch. Returns {"paused": False}
    if the switch has never been set — the honest default, not an error.
    """
    state = get_state("system", "dispatch_pause")
    if state is None:
        return {"paused": False}
    return {"paused": state["paused"], "reason": state.get("reason"), "paused_at": state.get("paused_at")}


def set_dispatch_pause(paused: bool, reason: Optional[str] = None) -> dict:
    """
    Flip the pause switch. The only intended caller today is
    nova_omen_dispatch.py's --pause/--resume CLI flags — there's no
    Controller UI yet to drive this remotely (86bax0wkj).
    """
    data = {
        "paused": paused,
        "reason": reason,
        "paused_at": datetime.now().isoformat(timespec="seconds") if paused else None,
    }
    write_state("system", "dispatch_pause", data)
    return data


# ── Escalation hook (stub) ───────────────────────────────────────

def check_escalation(session_result: dict) -> dict:
    """
    Stub — always returns {"escalation_needed": False}. Exists so
    dispatch_headless_task()'s return shape already carries an
    "escalation" key, letting real detection logic slot in later without
    touching the dispatch call site again. session_result is the same
    generic dict dispatch_headless_task() returns to its own caller.

    Intended landing spot for real-time scope-violation detection once
    86bax0wkj lands (e.g. flagging when a headless run acted outside its
    task's declared scope). Today that boundary is prompt/policy-only —
    see nova_task_queue.resolve_task_description()'s DATA-marker framing
    (86baxbt1x) — and this stub is only ever called after the SSH run has
    already completed, so it can't yet gate execution, only flag after
    the fact.
    """
    return {"escalation_needed": False}


# ── Quick test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print(is_dispatch_paused())
