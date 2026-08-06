# nova_orchestrator_devstral.py
# Devstral-Small-2507 backend for nova_orchestrator.py's coding sub-agent
# turn loop -- a second RunPod-hosted alternative to the Claude-API-backed
# inline loop, alongside nova_orchestrator_runpod.py's Qwen2.5-Coder-32B
# backend.
#
# Only imported by nova_orchestrator.py when framework_integrations.
# devstral_coding_agent is enabled in nova_config.json -- importing this
# module never happens when the flag is off (the default).
#
# Unlike nova_orchestrator_runpod.py's prompted <tools>-tag format (needed
# because Qwen2.5-Coder has no reliable vLLM tool-call parser), this backend
# uses REAL native tool-calling: OpenAI-style `tools`/`tool_choice` request
# params and a `tool_calls` array in the response, via
# nova_remote_inference_native_tools.chat_with_tools(). Confirmed live
# 2026-08-03 against the deployed endpoint (mistralai/Devstral-Small-2507,
# vLLM's `mistral` tool-call parser) that this produces a correctly
# populated tool_calls array, not just a 200 status.
#
# Message-history shape is genuinely different from the prompted-format
# backend's role="user" text wrapped in <tool_response> tags: this backend
# uses real OpenAI-style role="tool" messages carrying a tool_call_id, and
# an assistant message that called tools has content=None plus a separate
# tool_calls field. That's the reason this is a separate module rather than
# a second mode bolted onto nova_orchestrator_runpod.py -- every guard
# function below that doesn't care about message shape (duplicate-function/
# unreachable-code detection, file-allowlist/read-before-write/repeat-call
# refusal, write_file fallback nudges) is imported and reused unchanged;
# only the turn loop, system prompt, and context-window pruning are
# rewritten for the real message shape.

import json
from datetime import datetime

import nova_remote_inference_native_tools as native_inference
from nova_backend_profiles import DEVSTRAL_PROFILE
from nova_completion_gate import check_ground_truth_completion
from nova_config import is_framework_integration_enabled
from nova_laminar_client import log_turn as laminar_log_turn
from nova_langfuse_client import log_turn
from nova_orchestrator import _git_diff_against_master
from nova_orchestrator_runpod import (
    CODING_AGENT_CONTEXT_WINDOW_TOKENS,
    CONTEXT_SAFETY_MARGIN_TOKENS,
    GOAL_REANCHOR_INTERVAL_TURNS,
    GUARD_GOAL_REANCHOR,
    GUARD_NEAR_MISS_PARSE,  # noqa: F401 -- imported for parity/documentation; never fires on this backend, see module header
    GUARD_SELF_VERIFY_NUDGE,
    READ_BEFORE_WRITE_GUARD_PROMPT,
    SELF_VERIFICATION_PROMPT,
    _estimate_tokens,
    _execute_tool_guarded,
    _goal_reanchor_note,
    _in_scope_basenames,
    _log_guard_events,
    _log_runpod_cost_summary,
    build_condensed_system_prompt,
)

# ── Config ─────────────────────────────────────────────────────

# A single write_file tool call can carry a whole file's contents -- same
# reasoning as nova_orchestrator_runpod.CODING_AGENT_MAX_OUTPUT_TOKENS.
CODING_AGENT_MAX_OUTPUT_TOKENS = 8192


def build_devstral_system_prompt() -> str:
    """
    Same worktree-scoped preamble and condensed coding standards as
    nova_orchestrator_runpod.build_condensed_system_prompt(), plus the
    same read-before-write/self-verification behavioral guards -- but
    deliberately WITHOUT TOOLS_FORMAT_PROMPT, since that prompt exists only
    to teach a model the prompted <tools>-tag syntax; this backend's tool
    calling is native, described to the model via the real `tools` schema
    in the request itself, not prose.
    """
    return build_condensed_system_prompt() + READ_BEFORE_WRITE_GUARD_PROMPT + SELF_VERIFICATION_PROMPT


# ── Turn logging ─────────────────────────────────────────────────


def _log_agent_turn_devstral(
    slug: str,
    branch: str,
    turn: int,
    task: str,
    content: str | None,
    tool_calls: list[dict],
    usage: dict,
    skill_category: str | None,
    skill_version: str | None,
    pruned_pairs: int = 0,
    cost_usd: float | None = None,
) -> None:
    """
    Sibling to nova_orchestrator_runpod._log_agent_turn_runpod(), writing
    the identical agent_log.jsonl schema from this backend's real native
    response shape. No near_miss concept here (unlike the prompted-format
    backend) -- native tool-calling is parsed server-side by vLLM, so a
    malformed tool-call text burst structurally cannot happen; stop_reason
    is only ever "tool_use" or "end_turn".
    """
    from nova_orchestrator import AGENT_LOG_PATH

    stop_reason = "tool_use" if tool_calls else "end_turn"

    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "task_slug": slug,
        "branch": branch,
        "turn": turn,
        "task": task,
        "skill_category": skill_category,
        "skill_version": skill_version,
        "stop_reason": stop_reason,
        "tool_calls": [{"name": c.get("name"), "input": c.get("arguments")} for c in tool_calls],
        "input_tokens": usage.get("prompt_eval_count"),
        "output_tokens": usage.get("eval_count"),
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "model": native_inference.MODEL_NAME,
        "backend_profile": DEVSTRAL_PROFILE.name,
        "pruned_pairs": pruned_pairs,
        "cost_usd": cost_usd,
        "near_miss_count": 0,
    }
    with open(AGENT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    del content  # not logged today, same as nova_orchestrator._log_agent_turn's own omission of response text


# ── Context-window management ────────────────────────────────────

# Reuses CODING_AGENT_CONTEXT_WINDOW_TOKENS (32768, same context window the
# eval harness assumes for an apples-to-apples turn budget) and
# CONTEXT_SAFETY_MARGIN_TOKENS from nova_orchestrator_runpod, but needs its
# own estimation/pruning logic below: native tool-calling messages don't
# have a uniform "one string content field" shape (an assistant message
# that called tools has content=None plus a separate tool_calls list), and
# a single turn can append more than one tool-role message (one per tool
# call), so history can't be pruned in fixed 2-message pairs the way the
# prompted-format backend's _prune_history_if_needed() assumes.


def _estimate_message_tokens(message: dict) -> int:
    """Rough token estimate for one message, covering both a plain content string and a tool_calls list."""
    total = _estimate_tokens(message.get("content") or "")
    for call in message.get("tool_calls", []) or []:
        total += _estimate_tokens(json.dumps(call))
    return total


def _estimate_message_list_tokens(messages: list[dict]) -> int:
    return sum(_estimate_message_tokens(message) for message in messages)


def _message_groups(messages: list[dict]) -> list[tuple[int, int]]:
    """
    [(start, end), ...] for each turn group from index 2 onward: one
    assistant message plus every immediately-following non-assistant
    (tool-role) message before the next assistant message or the end of
    the list. Messages[0]/[1] (system prompt, original task) are never
    part of a group and are never pruned.
    """
    groups = []
    i = 2
    n = len(messages)
    while i < n:
        start = i
        i += 1
        while i < n and messages[i]["role"] != "assistant":
            i += 1
        groups.append((start, i))
    return groups


def _prune_history_if_needed(messages: list[dict], max_output_tokens: int) -> int:
    """
    Drops the oldest complete turn group(s) from `messages` in place until
    the estimated total fits this endpoint's real context window, or only
    one group remains (a single turn too large on its own isn't fixable by
    pruning -- the caller checks for that case separately). Same overflow-
    avoidance goal as nova_orchestrator_runpod._prune_history_if_needed(),
    adapted for this backend's variable-sized turn groups (see
    _message_groups()'s own docstring for why a fixed stride doesn't work
    here). Returns the number of groups pruned.
    """
    budget = CODING_AGENT_CONTEXT_WINDOW_TOKENS - max_output_tokens - CONTEXT_SAFETY_MARGIN_TOKENS
    pruned = 0

    while _estimate_message_list_tokens(messages) > budget:
        groups = _message_groups(messages)
        if len(groups) <= 1:
            break
        start, end = groups[0]
        del messages[start:end]
        pruned += 1

    if pruned:
        groups = _message_groups(messages)
        if groups:
            new_start, new_end = groups[0]
            # Never the assistant message itself (new_start) -- that would put
            # words in a past turn the model never actually said. The note goes
            # on the first tool-role message in the group, if one exists.
            if new_end - new_start > 1:
                note_index = new_start + 1
                note = f"[Note: {pruned} earlier turn(s) of tool output were removed to fit the context window.]\n\n"
                messages[note_index]["content"] = note + (messages[note_index].get("content") or "")

    return pruned


# ── Entry point used by nova_orchestrator.py ─────────────────────


def run_via_devstral(
    system_prompt: str,
    messages: list[dict],
    root: str,
    slug: str,
    branch_name: str,
    task_description: str,
    skill_category: str | None,
    skill_version: str | None,
    budget_gate_enabled: bool,
    max_turns: int,
    max_output_tokens: int,
    requirements: dict | None = None,
) -> tuple[str, int]:
    """
    Runs the turn loop via Nova's RunPod-hosted Devstral-Small-2507 endpoint,
    using real native tool-calling. Same (final_status, turns_used) contract
    as run_via_runpod()/run_via_langgraph(). `system_prompt` is accepted for
    interface parity with the other backends' call signature but ignored --
    this backend builds its own system prompt via build_devstral_system_
    prompt(), matching how run_via_runpod() also builds its own rather than
    trusting the Claude-lane system prompt (which assumes native Anthropic
    tool use and the full, oversized CLAUDE.md).
    """
    from nova_token_budget import get_budget_status

    del system_prompt  # see docstring -- this backend builds its own
    in_scope_basenames = _in_scope_basenames(requirements)

    messages = [
        {"role": "system", "content": build_devstral_system_prompt()},
        *messages,
    ]
    tools = native_inference.build_tool_schema()

    # Resolved once per task run -- see nova_orchestrator_runpod.
    # run_via_runpod()'s identical flag, whose docstring explains why this
    # needs to be requested per-call rather than assumed always-on.
    langfuse_tracing_enabled = is_framework_integration_enabled("langfuse_tracing")

    read_paths: set = set()
    failed_calls: set = set()
    failed_replace_counts: dict = {}
    edited_paths: set = set()
    verification_nudge_used = False
    total_cost_usd = 0.0
    total_execution_time_ms = 0
    guard_events: list = []

    final_status = "incomplete"
    turn = 0
    for turn in range(1, max_turns + 1):
        if budget_gate_enabled and get_budget_status().get("mode") == "halt":
            final_status = "stopped_budget_halt"
            break

        pairs_pruned = _prune_history_if_needed(messages, max_output_tokens)
        remaining_budget = CODING_AGENT_CONTEXT_WINDOW_TOKENS - max_output_tokens - CONTEXT_SAFETY_MARGIN_TOKENS
        if _estimate_message_list_tokens(messages) > remaining_budget:
            final_status = "stopped_context_overflow"
            break

        response = native_inference.chat_with_tools(
            messages,
            tools,
            max_tokens=max_output_tokens,
            logprobs=langfuse_tracing_enabled,
            top_logprobs=1 if langfuse_tracing_enabled else None,
        )
        if response is None:
            final_status = "stopped_devstral_call_failed"
            break

        content = response["content"]
        tool_calls = response["tool_calls"]
        finish_reason = response["finish_reason"]

        total_cost_usd += response.get("cost_usd") or 0.0
        total_execution_time_ms += response.get("execution_time_ms") or 0

        _log_agent_turn_devstral(
            slug,
            branch_name,
            turn,
            task_description,
            content,
            tool_calls,
            response,
            skill_category,
            skill_version,
            pruned_pairs=pairs_pruned,
            cost_usd=response.get("cost_usd"),
        )
        log_turn(
            branch_name,
            turn,
            DEVSTRAL_PROFILE.name,
            native_inference.MODEL_NAME,
            content,
            tool_calls,
            response.get("prompt_eval_count"),
            response.get("eval_count"),
            logprobs=response.get("logprobs"),
            cost_usd=response.get("cost_usd"),
        )
        # 86bb7qudh: additive alongside Langfuse, same normalized data, same
        # fail-open discipline -- see nova_laminar_client.log_turn()'s docstring.
        laminar_log_turn(
            branch_name,
            turn,
            DEVSTRAL_PROFILE.name,
            native_inference.MODEL_NAME,
            content,
            tool_calls,
            response.get("prompt_eval_count"),
            response.get("eval_count"),
            logprobs=response.get("logprobs"),
            cost_usd=response.get("cost_usd"),
        )

        assistant_message: dict = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                }
                for call in tool_calls
            ]
        messages.append(assistant_message)

        if not tool_calls:
            if finish_reason == "length":
                # Real max_output_tokens truncation -- distinguishable here
                # (unlike the prompted-format backend) via the native
                # finish_reason field itself, no eval_count-vs-cap inference
                # needed.
                final_status = "stopped_max_output_tokens"
                break

            if edited_paths and not verification_nudge_used:
                # Self-verification nudge (86bb71x2a), strengthened 2026-08-06 -- see
                # nova_orchestrator_runpod.py's own version of this block for the full
                # rationale (real bug found: the old "ran any command" condition let
                # real incompleteness sail through uncaught, 1 of 53 real runs ever
                # triggered it). Same fix here: reuse the exact same completeness
                # check the final gate runs, against the real diff-so-far.
                diff_so_far = _git_diff_against_master(root)
                gate_result = check_ground_truth_completion(
                    diff_so_far, task_description, root, requirements=requirements
                )
                real_issues = gate_result["hard_fails"] + gate_result["warnings"]

                if real_issues:
                    verification_nudge_used = True
                    guard_events.append(
                        {"guard": GUARD_SELF_VERIFY_NUDGE, "detail": f"turn {turn}: {'; '.join(real_issues)}"}
                    )
                    issues_text = "\n".join(f"- {issue}" for issue in real_issues)
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Before finishing: a completeness check found real issues "
                                f"with your work so far:\n{issues_text}\nAddress these "
                                "specifically before your final summary."
                            ),
                        }
                    )
                    continue

            final_status = "completed"
            break

        for call in tool_calls:
            name = call["name"]
            args = call["arguments"] or {}
            result = _execute_tool_guarded(
                name,
                args,
                root,
                slug,
                task_description,
                read_paths,
                failed_calls,
                failed_replace_counts,
                in_scope_basenames,
                guard_events,
            )
            if name in ("write_file", "file_replace") and not result.get("is_error", False):
                edited_paths.add(args.get("path", ""))
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result["content"]})

        if turn % GOAL_REANCHOR_INTERVAL_TURNS == 0:
            # Appended onto the last tool-role message just added, not as a
            # new message -- keeps _message_groups()'s grouping intact
            # (a bare trailing user-role reminder with no tool_calls would
            # otherwise be misread as the start of a new, empty group).
            guard_events.append({"guard": GUARD_GOAL_REANCHOR, "detail": f"turn {turn}"})
            messages[-1]["content"] += _goal_reanchor_note(task_description)
    else:
        final_status = "max_turns_reached"

    _log_runpod_cost_summary(
        slug, branch_name, task_description, final_status, turn, total_cost_usd, total_execution_time_ms
    )
    _log_guard_events(slug, branch_name, task_description, final_status, guard_events)

    return final_status, turn
