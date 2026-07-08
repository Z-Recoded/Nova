# nova_token_budget.py
# Token Budget Governor — scoped v1 (ClickUp 86barhqt9).
#
# Tracks the coding sub-agent's Claude API consumption against a configured
# ceiling, classifies the current mode (normal/conservative/critical/halt),
# and gives nova_orchestrator.py a hook to stop cleanly before overspending.
#
# The finalized spec (Nova Reference — Token Budget Governor Finalized
# Config, Addendum 2) assumes infrastructure that doesn't exist yet:
# nova_state.db (its own ClickUp task, 86bara3qe, is still "to do"), a push
# notification channel into an active Open WebUI chat session, and a
# ClickUp-driven task queue with Sonnet/Haiku routing and concurrency slots.
# This module implements only what's real today: the tracking formula, mode
# thresholds, and daily rollover. State persists to a local JSON file
# instead of nova_state.db. Haiku downgrade, task-queue awareness, and
# Open WebUI/ClickUp notifications are explicitly deferred — see CLAUDE.md's
# "Nova Coding Sub-Agent" section.
#
# Gated behind nova_config.json's framework_integrations.token_budget_governor
# flag (default off) — record_usage() is a no-op and get_budget_status()
# reports disabled until Marvin turns it on.

import json
import os
from datetime import date, datetime

from nova_config import is_framework_integration_enabled, load_config

STATE_PATH = "C:/Nova/logs/token_budget_state.json"

DEFAULT_STATE = {
    "consumed_session": 0.0,
    "consumed_today": 0.0,
    "today_date": None,
    "last_updated": None,
}

# What the finalized spec calls for that this v1 does not do — surfaced in
# get_budget_status() so the gap is always visible, not silently dropped.
NOT_YET_ENFORCED = [
    "Haiku downgrade in conservative mode (no task-type classifier exists)",
    "task-queue priority-aware selection / concurrency capping (no task queue exists)",
    "Open WebUI push notifications for critical/halt (no push channel exists)",
    "Automatic ClickUp task status update on halt",
]


# ── State persistence ────────────────────────────────────────────

def _load_state() -> dict:
    """Read the persisted token-budget state, or a fresh default if missing/corrupt."""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_STATE)


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _roll_daily_reset(state: dict) -> dict:
    """Zero consumed_today if the calendar day has changed since the last update."""
    today_str = date.today().isoformat()
    if state.get("today_date") != today_str:
        state["consumed_today"] = 0.0
        state["today_date"] = today_str
    return state


def reset_session() -> None:
    """
    Zero out consumed_session. There's no persistent orchestrator process
    today to auto-detect a session boundary, so this must be called
    explicitly (e.g. at the start of a new work session) — a documented
    limitation, not invented behavior.
    """
    state = _roll_daily_reset(_load_state())
    state["consumed_session"] = 0.0
    _save_state(state)


# ── Tracking ─────────────────────────────────────────────────────

def record_usage(usage) -> dict:
    """
    Record one API call's token usage against the running session/daily
    totals, using the finalized tracking formula (cache reads discounted
    0.1x, since Anthropic's cache-read pricing is roughly 1/10th of full
    input pricing — this keeps the budget percentage aligned with actual
    dollar cost, not raw token count). `usage` is an Anthropic
    response.usage object (or anything with the same four attributes).
    A no-op if token_budget_governor is disabled.
    """
    if not is_framework_integration_enabled("token_budget_governor"):
        return _load_state()

    state = _roll_daily_reset(_load_state())
    consumed = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_creation_input_tokens
        + usage.cache_read_input_tokens * 0.1
    )
    state["consumed_session"] += consumed
    state["consumed_today"] += consumed
    state["last_updated"] = datetime.now().isoformat(timespec="seconds")
    _save_state(state)
    return state


# ── Mode classification ─────────────────────────────────────────

def get_mode(consumed_session: float, consumed_today: float, config: dict) -> str:
    """
    Classify the current mode from the finalized threshold table:
    normal 0-50%, conservative 50-70%, critical 70-85%, halt 85-100% of
    session_ceiling. A daily_ceiling breach forces halt early regardless
    of session_pct, per the finalized spec.
    """
    session_ceiling = config.get("session_ceiling") or 0
    session_pct = consumed_session / session_ceiling if session_ceiling else 0.0

    daily_ceiling = config.get("daily_ceiling") or 0
    if daily_ceiling and consumed_today >= daily_ceiling:
        return "halt"

    if session_pct >= config.get("halt_threshold_pct", 1.0):
        return "halt"
    if session_pct >= config.get("critical_threshold_pct", 1.0):
        return "critical"
    if session_pct >= config.get("conservative_threshold_pct", 1.0):
        return "conservative"
    return "normal"


# ── Reporting ──────────────────────────────────────────────────

def get_budget_status() -> dict:
    """
    Full current token-budget status: consumption, ceilings, mode, and
    which parts of the finalized spec are actually enforced today. This is
    what nova_headroom.py folds into GET /headroom, and what
    nova_orchestrator.py checks before starting each new turn.
    """
    if not is_framework_integration_enabled("token_budget_governor"):
        return {"enabled": False, "reason": "token_budget_governor flag is off in nova_config.json"}

    config = load_config().get("token_budget", {})
    if not config:
        return {"enabled": False, "reason": "no token_budget block in nova_config.json"}

    state = _roll_daily_reset(_load_state())
    consumed_session = state["consumed_session"]
    consumed_today = state["consumed_today"]
    mode = get_mode(consumed_session, consumed_today, config)

    session_ceiling = config.get("session_ceiling") or 0
    daily_ceiling = config.get("daily_ceiling") or 0

    return {
        "enabled": True,
        "mode": mode,
        "session_ceiling": session_ceiling,
        "consumed_session": round(consumed_session),
        "session_pct": round(consumed_session / session_ceiling * 100, 1) if session_ceiling else None,
        "daily_ceiling": daily_ceiling,
        "consumed_today": round(consumed_today),
        "daily_pct": round(consumed_today / daily_ceiling * 100, 1) if daily_ceiling else None,
        "last_updated": state["last_updated"],
        "not_yet_enforced": NOT_YET_ENFORCED,
    }


# ── Quick test ─────────────────────────────────────────────────
if __name__ == "__main__":
    print(json.dumps(get_budget_status(), indent=2))
