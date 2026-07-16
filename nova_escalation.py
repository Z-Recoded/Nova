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
#
# Cross-machine fix (2026-07-16): these two functions used to import
# nova_state.py directly. That broke the moment anything checked pause
# state natively on the Omen (the scheduled-dispatch cron job — see
# nova_scheduled_dispatch.py) — nova_state.py's DB_PATH is a hardcoded
# Windows path that silently resolves to a disconnected file on Linux, so
# a pause set from the Aero was invisible there. Confirmed live: found the
# Omen's own accidental file at
# /home/marvinroyal5/nova/C:/Nova/nova_state.db, separate from the Aero's
# real one. Fixed by routing through the Omen's own nova_api.py
# (POST/GET /dispatch-pause) instead — the same canonical-FastAPI-layer
# pattern the activity profile already uses, matching this project's
# Golden Rule that FastAPI is the only interface other components talk
# to. Function signatures and return shapes are unchanged, so
# nova_omen_dispatch.py's existing --pause/--resume calls needed no edits.

import json
import os
import urllib.error
import urllib.request
from typing import Optional

NOVA_API_URL = os.environ.get("NOVA_API_URL", "http://100.114.197.117:8001")


# ── Pause-at-will ────────────────────────────────────────────────

def is_dispatch_paused() -> dict:
    """
    Current pause state for headless dispatch, read from the Omen's own
    nova-api. Fails toward paused=True on any network/API error — if the
    pause state can't be confirmed, treat it as paused rather than risk
    running while Marvin intended to block it. This is the one case in
    this module where failing safe means failing restrictive, not
    permissive (compare nova_omen_dispatch.choose_fuel_source(), which
    fails toward the metered key on ambiguity — same "don't assume the
    safe case" instinct, applied to a different question).
    """
    try:
        with urllib.request.urlopen(f"{NOVA_API_URL}/dispatch-pause", timeout=10) as response:
            state = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        return {
            "paused": True,
            "reason": f"Pause state could not be confirmed ({e}) — failing toward paused for safety.",
            "paused_at": None,
        }
    return {"paused": state.get("paused", False), "reason": state.get("reason"), "paused_at": state.get("paused_at")}


def set_dispatch_pause(paused: bool, reason: Optional[str] = None) -> dict:
    """
    Flip the pause switch via the Omen's nova-api. Unlike the read path,
    this does not fail silently — if Marvin explicitly asks to pause and
    the request fails, he needs to see that, not have it swallowed.
    """
    payload = json.dumps({"paused": paused, "reason": reason}).encode("utf-8")
    request = urllib.request.Request(
        f"{NOVA_API_URL}/dispatch-pause",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


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
