# nova_config.py
# Feature flag system — the single source of truth for which classical
# algorithm augmentations and framework integrations are currently active.
# All future augments (A* retrieval, DP context packing, priority queue
# routing, two-tier memory decay, LangGraph, OpenHands, visual retrieval)
# gate their logic through this module instead of hardcoding behavior, so
# each can be isolated on/off for controlled benchmarking. See CLAUDE.md
# Phase 1.75 and Nova Reference Section 25 Addendum (25F) for the design.

import json
import os
from typing import TypedDict


class FlagMeta(TypedDict):
    path: list[str]
    label: str
    category: str
    aero_only: bool


# ── Config ─────────────────────────────────────────────────────
# Resolved relative to this file's own location, not a hardcoded Windows
# path — same class of bug already found and fixed twice elsewhere in this
# project (nova_orchestrator.py's dotenv path, nova_api.py's GRAPH_PATH).
# Confirmed live 2026-07-16: the old hardcoded "C:/Nova/nova_config.json"
# silently fell back to DEFAULT_CONFIG on the Omen (nova_scheduled_dispatch.py
# runs there natively, not over SSH from the Aero) even though the real file
# already existed in its own checkout — every flag read there was reading a
# baked-in default, not the real config, with zero error signal.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova_config.json")

# Safe fallback if nova_config.json is missing or malformed — every augment
# stays off, which matches Nova's current unaugmented behavior exactly.
DEFAULT_CONFIG = {
    "classical_augments": {
        "enabled": False,
        "astar_retrieval": False,
        "dp_context_packing": False,
        "priority_queue_routing": False,
        "memory_decay_weighting": False,
        "memory_decay_tiers": {
            "task_trail": False,
            "architectural_anchors": False,
        },
    },
    "framework_integrations": {
        "langgraph_orchestration": False,
        "openhands_coding_agent": False,
        "visual_retrieval": False,
        # "token_budget_governor" is a boolean feature flag, not a password.
        "token_budget_governor": False,  # nosec B105
        "skill_injection": False,
        "remote_gpu_inference": False,
    },
    "model_routing": {
        "enabled": False,
        "routes": {},
        "default_model": "llama3.2",
    },
    "scheduled_dispatch": {
        "review_backpressure_enabled": False,
        "max_unreviewed_dispatches": 3,
        "sandboxed_dispatch_enabled": False,
    },
    "push_notifications": {
        "enabled": False,
    },
    "pre_action_approval_gate": {
        "enabled": False,
        "command_patterns": [
            "pip install",
            "npm install",
            "pip uninstall",
            "npm uninstall",
            "git commit --amend",
            "git rebase",
            "curl ",
            "wget ",
        ],
        "max_files_per_turn": None,
        "timeout_seconds": 300,
        "poll_interval_seconds": 3,
    },
}


# ── Loading ────────────────────────────────────────────────────


def load_config() -> dict:
    """
    Read nova_config.json fresh from disk every call — no in-memory caching.
    This is what makes flags hot-switchable: toggling the file takes effect
    on the very next query, not just at startup. The file is tiny, so
    re-reading it per query costs nothing worth optimizing away.
    Falls back to DEFAULT_CONFIG (everything off) if the file is missing
    or not valid JSON, since this file is hand-edited and sits on the live
    query path.
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_CONFIG


# ── Flag checks ────────────────────────────────────────────────


def is_augment_enabled(flag_name: str) -> bool:
    """
    True only if the classical_augments master switch AND the named flag
    are both on. The master switch returns Nova to vanilla mode regardless
    of individual flag state — it is the single override for "back to
    baseline," per the feature flag spec.
    """
    augments = load_config().get("classical_augments", {})
    return bool(augments.get("enabled")) and bool(augments.get(flag_name))


def is_memory_decay_tier_enabled(tier_name: str) -> bool:
    """
    True only if the classical_augments master switch, memory_decay_weighting,
    and the named tier (task_trail | architectural_anchors) are all on. Tiers
    are meaningless without memory_decay_weighting itself being active.
    """
    augments = load_config().get("classical_augments", {})
    if not augments.get("enabled") or not augments.get("memory_decay_weighting"):
        return False
    tiers = augments.get("memory_decay_tiers", {})
    return bool(tiers.get(tier_name))


def is_framework_integration_enabled(flag_name: str) -> bool:
    """
    True if the named framework integration flag is on. These are
    independent framework/agent swaps (LangGraph, OpenHands, visual
    retrieval) rather than classical algorithm augments, so there is no
    shared master switch to check here.
    """
    integrations = load_config().get("framework_integrations", {})
    return bool(integrations.get(flag_name))


def is_model_routing_enabled() -> bool:
    """True if per-category model routing is on. Independent flag, no shared master switch."""
    return bool(load_config().get("model_routing", {}).get("enabled"))


def is_review_backpressure_enabled() -> bool:
    """
    True if nova_scheduled_dispatch.py's review-bandwidth cap is on
    (86bawpvzz implication #2) — independent flag, no shared master switch.
    Default off, matching every other augment/integration in this file.
    """
    return bool(load_config().get("scheduled_dispatch", {}).get("review_backpressure_enabled"))


def is_sandboxed_dispatch_enabled() -> bool:
    """
    True if nova_scheduled_dispatch.py's cron loop should run each dispatch
    through nova_omen_dispatch.dispatch_headless_task_sandboxed() (real
    Docker containment) instead of dispatch_headless_task() (bare SSH, no
    containment) — independent flag, no shared master switch. Default off:
    the sandboxed path was proven manually first (two real live dispatches,
    see its own docstring) but was deliberately not trusted in the fully
    unattended cron path until confirmed here explicitly.
    """
    return bool(load_config().get("scheduled_dispatch", {}).get("sandboxed_dispatch_enabled"))


def is_push_notifications_enabled() -> bool:
    """
    True if nova_notify.send_notification() should actually POST to ntfy.sh
    (86bb3ceyp) — independent flag, no shared master switch. Default off:
    even with this on, send_notification() still no-ops silently if
    NTFY_TOPIC isn't set in .env, so flipping this alone is harmless.
    """
    return bool(load_config().get("push_notifications", {}).get("enabled"))


def is_pre_action_approval_gate_enabled() -> bool:
    """
    True if nova_orchestrator.py's Aero-lane turn loop should pause and wait
    for a human decision before executing a tool call that matches
    get_approval_gate_patterns() (86bb3ceym) — independent flag, no shared
    master switch. Aero-only: the Omen's headless dispatch lane bypasses
    nova_orchestrator.py entirely and is not covered by this gate at all
    (see CLAUDE.md's Pre-Action Approval Gate subsection).
    """
    return bool(load_config().get("pre_action_approval_gate", {}).get("enabled"))


def get_approval_gate_patterns() -> list[str]:
    """Substring command patterns that pause for approval instead of executing silently."""
    return load_config().get("pre_action_approval_gate", {}).get("command_patterns", [])


def get_approval_gate_max_files_per_turn() -> int | None:
    """
    File-edit-count gate threshold, or None if that trigger is disabled.
    Deliberately a fast-follow, not wired into _approval_gate_reason() yet —
    the command-pattern trigger ships and gets proven live first.
    """
    return load_config().get("pre_action_approval_gate", {}).get("max_files_per_turn")


def get_approval_gate_timeout_seconds(fallback: int = 300) -> int:
    """How long _request_tool_approval() waits for a decision before failing closed (denied)."""
    return load_config().get("pre_action_approval_gate", {}).get("timeout_seconds", fallback)


def get_approval_gate_poll_interval_seconds(fallback: int = 3) -> int:
    """How often _request_tool_approval() re-checks nova_state.db for a decision."""
    return load_config().get("pre_action_approval_gate", {}).get("poll_interval_seconds", fallback)


def get_max_unreviewed_dispatches(fallback: int = 3) -> int:
    """
    The review-backpressure cap threshold — nova_scheduled_dispatch.py
    skips picking a new task once this many past dispatches have a real
    session_id but no matching review decision yet. Falls back to
    `fallback` if the config value is missing, same pattern as
    get_routed_model()'s fallback argument.
    """
    return load_config().get("scheduled_dispatch", {}).get("max_unreviewed_dispatches", fallback)


def get_routed_model(category: str, fallback: str) -> str:
    """
    Look up which Ollama model to use for a given nova_router.py category.
    Returns `fallback` unchanged whenever routing is disabled, so callers that
    pass their own current model constant as `fallback` see zero behavior
    change while the flag is off. When enabled, looks up `category` in
    model_routing.routes, falling back to model_routing.default_model if the
    category has no entry, and to `fallback` itself if even that is missing.
    """
    if not is_model_routing_enabled():
        return fallback
    routing = load_config().get("model_routing", {})
    default_model = routing.get("default_model", fallback)
    return routing.get("routes", {}).get(category, default_model)


# ── Flag registry (Controller switches panel, 86bb3ceyX) ────────
# Explicit allowlist of the nova_config.json flags exposed for live
# viewing/toggling from the Nova Controller — same discipline as this
# codebase's other explicit allowlists (nova_state.py's KNOWN_ENTITIES,
# nova_log_rotation.py's ROTATABLE_LOGS, nova_tools.py's
# DANGEROUS_COMMAND_PATTERNS). Never a generic "set any JSON path" route:
# a flag has to be named here to be toggleable at all. dispatch_pause is
# deliberately NOT in this registry — it lives in nova_state.db, not this
# file, with its own already-working GET/POST /dispatch-pause mechanism;
# nova_api.py's /flags routes handle it as a special case alongside these.
#
# "path" is the exact nested-key route into load_config()'s dict. "aero_only"
# marks flags only ever read by code that runs on the Aero
# (nova_orchestrator.py's interactive loop) — toggling these from the
# Omen-served Controller changes the Omen's own file immediately, but the
# Aero won't see it until that machine's next `git pull`, same accepted gap
# as the in-flight-status widget's lane scoping (86bb3cey0).
FLAG_REGISTRY: dict[str, FlagMeta] = {
    "sandboxed_dispatch_enabled": {
        "path": ["scheduled_dispatch", "sandboxed_dispatch_enabled"],
        "label": "Docker Sandbox for Auto-Dispatch",
        "category": "operational_safety",
        "aero_only": False,
    },
    "review_backpressure_enabled": {
        "path": ["scheduled_dispatch", "review_backpressure_enabled"],
        "label": "Pause on Review Backlog",
        "category": "operational_safety",
        "aero_only": False,
    },
    "token_budget_governor": {
        "path": ["framework_integrations", "token_budget_governor"],
        "label": "Enforce API Spend Limit",
        "category": "spend_governor",
        "aero_only": True,
    },
    "classical_augments_enabled": {
        "path": ["classical_augments", "enabled"],
        "label": "Experimental Retrieval Algorithms",
        "category": "scaffolding",
        "aero_only": False,
    },
    "langgraph_orchestration": {
        "path": ["framework_integrations", "langgraph_orchestration"],
        "label": "LangGraph Task Runner",
        "category": "scaffolding",
        "aero_only": True,
    },
    "openhands_coding_agent": {
        "path": ["framework_integrations", "openhands_coding_agent"],
        "label": "OpenHands Auto-Coder",
        "category": "scaffolding",
        "aero_only": True,
    },
    "push_notifications_enabled": {
        "path": ["push_notifications", "enabled"],
        "label": "Push Notifications (ntfy.sh)",
        "category": "operational_safety",
        "aero_only": False,
    },
    "pre_action_approval_gate_enabled": {
        "path": ["pre_action_approval_gate", "enabled"],
        "label": "Pre-Action Approval Gate (coding agent)",
        "category": "operational_safety",
        "aero_only": True,
    },
}


def _resolve_path(config: dict, path: list[str]):
    """Walk a nested-key path into a dict, returning None if any segment is missing."""
    node = config
    for segment in path:
        if not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]
    return node


def get_flag_registry_values() -> dict:
    """
    Current value of every registered flag, keyed by flag_key — backs
    GET /flags. Reads via load_config() (already hot/uncached), so this
    always reflects the file's real current state, never a stale copy.
    """
    config = load_config()
    return {
        key: {
            "value": bool(_resolve_path(config, meta["path"])),
            "label": meta["label"],
            "category": meta["category"],
            "aero_only": meta["aero_only"],
        }
        for key, meta in FLAG_REGISTRY.items()
    }


def set_flag_value(flag_key: str, value: bool) -> dict:
    """
    Write one registered flag's value to nova_config.json and return the
    updated registry snapshot. Raises KeyError for an unregistered key —
    callers (nova_api.py's /flags route) turn that into a 404, not a
    silent no-op. Takes effect immediately for any code on this machine
    (load_config() re-reads fresh, see its own docstring) — persisting
    that across machines/restarts is the caller's job (nova_api.py commits
    and pushes the file in the background after this returns).
    """
    if flag_key not in FLAG_REGISTRY:
        raise KeyError(f"'{flag_key}' is not a registered flag")

    config = load_config()
    path = FLAG_REGISTRY[flag_key]["path"]
    node = config
    for segment in path[:-1]:
        node = node.setdefault(segment, {})
    node[path[-1]] = bool(value)

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    return get_flag_registry_values()


# ── Reporting ──────────────────────────────────────────────────


def config_snapshot() -> dict:
    """
    Flat snapshot of every flag's current value, for attaching to per-query
    telemetry (see nova_log.log_query). One source of truth for "what was
    on when this query ran" — feeds the Nova Log benchmark dashboard once
    augments start flipping these flags for controlled experiments.
    """
    config = load_config()
    augments = config.get("classical_augments", {})
    tiers = augments.get("memory_decay_tiers", {})
    integrations = config.get("framework_integrations", {})
    model_routing = config.get("model_routing", {})

    return {
        "classical_augments_enabled": bool(augments.get("enabled")),
        "astar_retrieval": bool(augments.get("astar_retrieval")),
        "dp_context_packing": bool(augments.get("dp_context_packing")),
        "priority_queue_routing": bool(augments.get("priority_queue_routing")),
        "memory_decay_weighting": bool(augments.get("memory_decay_weighting")),
        "memory_decay_task_trail": bool(tiers.get("task_trail")),
        "memory_decay_architectural_anchors": bool(tiers.get("architectural_anchors")),
        "langgraph_orchestration": bool(integrations.get("langgraph_orchestration")),
        "openhands_coding_agent": bool(integrations.get("openhands_coding_agent")),
        "visual_retrieval": bool(integrations.get("visual_retrieval")),
        "token_budget_governor": bool(integrations.get("token_budget_governor")),
        "skill_injection": bool(integrations.get("skill_injection")),
        "remote_gpu_inference": bool(integrations.get("remote_gpu_inference")),
        "model_routing_enabled": bool(model_routing.get("enabled")),
    }


# ── Quick test ─────────────────────────────────────────────────
if __name__ == "__main__":
    config = load_config()
    print(json.dumps(config, indent=2))
    print("\nSnapshot:")
    print(json.dumps(config_snapshot(), indent=2))
    print(f"\nastar_retrieval enabled: {is_augment_enabled('astar_retrieval')}")
    print(f"langgraph_orchestration enabled: {is_framework_integration_enabled('langgraph_orchestration')}")
