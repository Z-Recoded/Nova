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
CONFIG_PATH = "C:/Nova/nova_config.json"

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
        "token_budget_governor": False,
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
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
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
    }


# ── Quick test ─────────────────────────────────────────────────
if __name__ == "__main__":
    config = load_config()
    print(json.dumps(config, indent=2))
    print("\nSnapshot:")
    print(json.dumps(config_snapshot(), indent=2))
    print(f"\nastar_retrieval enabled: {is_augment_enabled('astar_retrieval')}")
    print(f"langgraph_orchestration enabled: {is_framework_integration_enabled('langgraph_orchestration')}")
