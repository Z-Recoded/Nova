# nova_orchestrator_graph.py
# LangGraph port of nova_orchestrator.py's per-task turn loop.
#
# Scope note: nova_orchestrator.py today runs exactly one coding task through a
# single linear turn loop — no parallel or multi-task orchestration exists yet
# (that's a future direction, not built). This module wraps that existing
# single-task loop as a LangGraph graph, preserving its behavior exactly. It is
# the foundation future parallel/multi-task orchestration builds on later, not
# an implementation of that future itself.
#
# Only imported by nova_orchestrator.py when framework_integrations.
# langgraph_orchestration is enabled in nova_config.json — importing this
# module (and therefore langgraph) never happens when the flag is off, so a
# missing/broken langgraph install can't affect Nova while the feature is
# disabled (the default).

from typing import TypedDict

from langgraph.graph import END, StateGraph

from nova_token_budget import get_budget_status, record_usage


# ── State ──

class AgentTurnState(TypedDict):
    """
    Everything the turn loop reads or mutates across nodes.

    `messages`, `turn`, `stop_reason`, and `final_status` change every cycle.
    The rest are read-once context, set before the graph runs and never
    mutated by a node — carried in state only because LangGraph nodes only
    receive state, not arbitrary closures.
    """
    messages: list
    turn: int
    stop_reason: str | None
    final_status: str | None
    client: object
    system_prompt: str
    root: str
    slug: str
    branch_name: str
    task_description: str
    skill_category: str | None
    skill_version: str | None
    budget_gate_enabled: bool
    max_turns: int
    model: str
    max_tokens: int


# ── Nodes ──

def _check_budget(state: AgentTurnState) -> AgentTurnState:
    """
    The two gates nova_orchestrator.py's inline loop checks before every API
    call: has the token budget governor called for a halt, and has the turn
    cap been reached. Mirrors the original loop's `for...else` max-turns
    behavior explicitly, rather than relying on LangGraph's recursion_limit
    (which would raise instead of setting a final_status gracefully).
    """
    if state["budget_gate_enabled"] and get_budget_status().get("mode") == "halt":
        return {**state, "final_status": "stopped_budget_halt"}
    if state["turn"] >= state["max_turns"]:
        return {**state, "final_status": "max_turns_reached"}
    return state


def _call_model(state: AgentTurnState, log_agent_turn) -> AgentTurnState:
    """
    One turn: call Claude, log it, record budget usage, append the response,
    and decide whether the run is done. Identical call shape and stop_reason
    handling to nova_orchestrator.py's original inline loop.
    """
    turn = state["turn"] + 1
    response = state["client"].messages.create(
        model=state["model"],
        max_tokens=state["max_tokens"],
        system=[
            {"type": "text", "text": state["system_prompt"], "cache_control": {"type": "ephemeral"}},
        ],
        tools=_tool_definitions(),
        messages=state["messages"],
    )

    log_agent_turn(
        state["slug"], state["branch_name"], turn, state["task_description"],
        response, state["skill_category"], state["skill_version"],
    )
    if state["budget_gate_enabled"]:
        record_usage(response.usage)

    messages = state["messages"] + [{"role": "assistant", "content": response.content}]

    final_status = None
    if response.stop_reason == "end_turn":
        final_status = "completed"
    elif response.stop_reason != "tool_use":
        # e.g. "max_tokens" — response (possibly mid tool-call) got cut off.
        # Stop and surface it honestly rather than executing a truncated call.
        final_status = f"stopped_{response.stop_reason}"

    return {
        **state,
        "turn": turn,
        "messages": messages,
        "stop_reason": response.stop_reason,
        "final_status": final_status,
    }


def _execute_tools(state: AgentTurnState, execute_tool) -> AgentTurnState:
    """
    Runs every tool_use block from the most recent assistant message and
    appends the tool_result message. Only reached when stop_reason is
    "tool_use". Identical dispatch to nova_orchestrator.py's original loop.
    """
    last_message = state["messages"][-1]["content"]
    tool_results = []
    for block in last_message:
        if block.type != "tool_use":
            continue
        result = execute_tool(block.name, block.input, state["root"])
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": result["content"],
            "is_error": result.get("is_error", False),
        })

    messages = state["messages"] + [{"role": "user", "content": tool_results}]
    return {**state, "messages": messages}


# ── Graph construction ──

def _tool_definitions():
    """Deferred import so this module never needs nova_orchestrator.py at import time for its constant alone."""
    from nova_orchestrator import TOOL_DEFINITIONS
    return TOOL_DEFINITIONS


def build_graph(log_agent_turn, execute_tool):
    """
    Wires the three nodes into the same shape as nova_orchestrator.py's
    original loop: check gates, call the model, run tools if needed, repeat.
    `log_agent_turn`/`execute_tool` are passed in (not imported at module
    load) so this module has no import-time dependency on
    nova_orchestrator.py beyond the lazy TOOL_DEFINITIONS lookup above.
    """
    graph = StateGraph(AgentTurnState)

    graph.add_node("check_budget", _check_budget)
    graph.add_node("call_model", lambda state: _call_model(state, log_agent_turn))
    graph.add_node("execute_tools", lambda state: _execute_tools(state, execute_tool))

    graph.set_entry_point("check_budget")

    graph.add_conditional_edges(
        "check_budget",
        lambda state: "end" if state["final_status"] else "continue",
        {"end": END, "continue": "call_model"},
    )
    graph.add_conditional_edges(
        "call_model",
        lambda state: "end" if state["final_status"] else "continue",
        {"end": END, "continue": "execute_tools"},
    )
    graph.add_edge("execute_tools", "check_budget")

    return graph.compile()


# ── Entry point used by nova_orchestrator.py ──

def run_via_langgraph(
    client, system_prompt, messages, root, slug, branch_name, task_description,
    skill_category, skill_version, budget_gate_enabled, max_turns, model, max_tokens,
    log_agent_turn, execute_tool,
) -> tuple[str, int]:
    """
    Runs the turn loop via the LangGraph graph instead of the inline loop,
    with identical inputs/outputs to nova_orchestrator.py's original code.
    Returns (final_status, turns_used).
    """
    graph = build_graph(log_agent_turn, execute_tool)

    initial_state: AgentTurnState = {
        "messages": messages,
        "turn": 0,
        "stop_reason": None,
        "final_status": None,
        "client": client,
        "system_prompt": system_prompt,
        "root": root,
        "slug": slug,
        "branch_name": branch_name,
        "task_description": task_description,
        "skill_category": skill_category,
        "skill_version": skill_version,
        "budget_gate_enabled": budget_gate_enabled,
        "max_turns": max_turns,
        "model": model,
        "max_tokens": max_tokens,
    }

    # recursion_limit needs headroom for two graph "steps" (check_budget +
    # call_model/execute_tools) per logical turn, plus a safety margin.
    final_state = graph.invoke(initial_state, config={"recursion_limit": max_turns * 3 + 10})

    return final_state["final_status"], final_state["turn"]
