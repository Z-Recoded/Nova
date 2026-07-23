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
