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
# check_escalation() is real as of 86bax0wkj (2026-07-18) — it parses a
# structured NOVA_ESCALATION_START/END block out of the dispatch/resume
# result's own summary text via regex. It takes the generic result dict
# dispatch_headless_task()/resume_headless_task() already return
# (success/session_id/summary/stop_reason), not raw Claude Code CLI
# session internals, per 86bax0exx's requirement that the escalation
# interface stay backend-agnostic (Claude Code CLI today,
# OpenHands+local-model later). The pause/package/notify/wait/resume flow
# this feeds lives in nova_scheduled_dispatch.py's _handle_escalation()
# and nova_api.py's /escalations routes — see CLAUDE.md's "Escalation
# Protocol" subsection for the exact block format headless tasks emit.
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
import re
import urllib.error
import urllib.request

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
        # Fixed internal API URL, never user-controlled -- not the file:/custom-scheme risk this check targets.
        with urllib.request.urlopen(  # nosec B310
            f"{NOVA_API_URL}/dispatch-pause", timeout=10
        ) as response:
            state = json.loads(response.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        return {
            "paused": True,
            "reason": f"Pause state could not be confirmed ({e}) — failing toward paused for safety.",
            "paused_at": None,
        }
    return {"paused": state.get("paused", False), "reason": state.get("reason"), "paused_at": state.get("paused_at")}


def set_dispatch_pause(paused: bool, reason: str | None = None) -> dict:
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
    # Fixed internal API URL, never user-controlled -- not the file:/custom-scheme risk this check targets.
    with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
        return json.loads(response.read())


# ── Escalation hook ──────────────────────────────────────────────

_ESCALATION_BLOCK = re.compile(r"NOVA_ESCALATION_START(.*?)NOVA_ESCALATION_END", re.DOTALL)
_QUESTION = re.compile(r"QUESTION:\s*(.*?)(?:\n\s*OPTIONS:|\n\s*CONTEXT:|\Z)", re.DOTALL)
_OPTIONS = re.compile(r"OPTIONS:\s*(.*?)(?:\n\s*CONTEXT:|\Z)", re.DOTALL)
_CONTEXT = re.compile(r"CONTEXT:\s*(.*)\Z", re.DOTALL)


def check_escalation(session_result: dict) -> dict:
    """
    Parses a NOVA_ESCALATION_START/END block out of session_result's own
    "summary" text (the same generic dict dispatch_headless_task()/
    resume_headless_task() return), per CLAUDE.md's Escalation Protocol
    format. Pure parsing, no I/O — persistence is the caller's job
    (nova_scheduled_dispatch.py's _handle_escalation()), keeping this
    reusable from both the dispatch and resume paths.

    Returns {"escalation_needed": False} when no block is found. Returns
    {"escalation_needed": True, "question": str|None, "options_considered":
    list[str], "context": str|None, "malformed": bool} when found —
    malformed=True (question is None) if the block exists but QUESTION:
    didn't parse, so a real attempt with a formatting slip surfaces to
    Marvin rather than silently vanishing — the same fail-toward-the-
    restrictive-case instinct as is_dispatch_paused()'s fail-toward-paused.

    Still only ever called after the SSH run has already completed, so it
    can't gate execution mid-run — only flag after the fact that the task
    paused itself and is waiting on an answer.
    """
    match = _ESCALATION_BLOCK.search(session_result.get("summary") or "")
    if not match:
        return {"escalation_needed": False}

    block = match.group(1)

    question_match = _QUESTION.search(block)
    question = question_match.group(1).strip() if question_match else ""

    options_considered = []
    options_match = _OPTIONS.search(block)
    if options_match:
        for line in options_match.group(1).splitlines():
            line = line.strip()
            if line.startswith("- "):
                options_considered.append(line[2:].strip())

    context_match = _CONTEXT.search(block)
    context = context_match.group(1).strip() if context_match else None

    return {
        "escalation_needed": True,
        "question": question or None,
        "options_considered": options_considered,
        "context": context or None,
        "malformed": not bool(question),
    }


# ── Quick test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print(is_dispatch_paused())
