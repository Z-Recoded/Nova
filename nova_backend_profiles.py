# nova_backend_profiles.py
# Per-backend conventions for Nova's coding sub-agent turn loop (86bb71x1j,
# Level 1) -- generalizes the ad-hoc Qwen tool-call-parser fix (86bb71x02)
# into a small, explicit profile per backend instead of each backend module
# hardcoding its own conventions with no shared shape to compare against.
#
# NOT YET WIRED into the orchestrator's actual dispatch logic -- run_coding_
# task() still branches on nova_config.py's runpod_coding_agent flag exactly
# as before this file was added. Today this is real, load-bearing
# documentation of what already differs between backends (visible per-turn
# in agent_log.jsonl's backend_profile field), not a live dispatch
# mechanism. Rewriting the orchestrator to dynamically dispatch off these
# profiles is real, valuable, riskier follow-up work -- deliberately left
# for its own dedicated pass rather than folded into this one, matching the
# ticket's own framing ("forward-looking... not blocking current work").
#
# Level 2 (auto-probing a new backend's conventions via a calibration suite,
# instead of hand-writing a profile) is explicitly deferred -- not worth
# building for 2 backends, per the ticket. Worth building once Nova is
# onboarding a 3rd or 4th backend and hand-tuning stops being cheap.

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BackendProfile:
    """
    One backend's real conventions: how it's asked to call tools, how a
    finished turn is recognized, and which extra system-prompt fragments
    that convention requires. A future backend would fill in one of these
    by calibration (Level 2) instead of by hand, once hand-tuning profiles
    for each new addition stops being cheap.
    """

    name: str
    tool_call_format: str
    completion_signal: str
    edit_format: str
    extra_system_prompt_fragments: tuple[str, ...] = field(default_factory=tuple)


CLAUDE_PROFILE = BackendProfile(
    name="claude",
    tool_call_format="native_anthropic_tool_use",
    completion_signal="stop_reason_end_turn",
    edit_format="file_replace_search_replace_or_write_file",
)

# Real, tested convention from vLLM issue #32926/PR #32931 -- see
# nova_orchestrator_runpod.py's own header comment for the full story of why
# this backend needs a prompted format instead of native tool-calling.
RUNPOD_PROFILE = BackendProfile(
    name="runpod_qwen2.5_coder_32b",
    tool_call_format="prompted_tools_tag",
    completion_signal="no_tool_calls_in_response",
    edit_format="file_replace_search_replace_or_write_file",
    extra_system_prompt_fragments=(
        "TOOLS_FORMAT_PROMPT",
        "READ_BEFORE_WRITE_GUARD_PROMPT",
        "SELF_VERIFICATION_PROMPT",
    ),
)
