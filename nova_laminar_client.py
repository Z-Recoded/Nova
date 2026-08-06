# nova_laminar_client.py
# Laminar Cloud tracing for the coding sub-agent (Observability Initiative
# 86bb7pamh, sibling infra ticket 86bb7qudh). Mirrors nova_langfuse_client.py's
# log_turn()/log_guard_events()/log_gate_result() calling convention exactly,
# so both fire from the same three turn-loop call sites additively, but uses
# Laminar's own idiomatic SDK (spans + session grouping) rather than
# reproducing Langfuse's trace/score object shapes verbatim.
#
# Two real design differences from nova_langfuse_client.py, not oversights:
#
# 1. SESSION GROUPING, NOT A SEEDED TRACE ID. Langfuse's create_trace_id(seed=
#    branch_name) deterministically reuses one trace across every turn of a
#    task. Laminar's SDK has no equivalent deterministic trace-id seeding --
#    its own documented grouping mechanism is Laminar.set_trace_session_id(),
#    "reuse across turns/workflows" per the installed skill's own reference
#    doc (.claude/skills/laminar/references/instrumentation-python.md).
#    branch_name is used as the session_id here, same intent (every turn of
#    one coding task groups together in the UI) via Laminar's own idiom
#    instead of forcing Langfuse's mechanism onto a different SDK.
#
# 2. GUARD/GATE SIGNAL AS SPAN METADATA, NOT LANGFUSE-STYLE SCORES. Langfuse's
#    create_score() has no confirmed Laminar equivalent in the reference docs
#    installed here -- SKILL.md mentions LaminarClient.tags.tag(trace_id,
#    ...) only in passing, with no parameter shape given. The skill's own
#    ground rule is explicit: "Don't guess APIs. If unsure, consult the
#    reference file or ask." Rather than guess at tags.tag()'s signature,
#    guard/gate registry codes are logged as span input/output/metadata
#    instead -- a confirmed, fully-documented mechanism, queryable via the
#    SQL API's `spans` table exactly like Langfuse's scores were meant to be
#    queried (`lmnr-cli sql query "SELECT * FROM spans WHERE name =
#    'guard_events' ..."`).
#
# GUARD_TO_REGISTRY_CODE/FINAL_STATUS_TO_REGISTRY_CODE/GATE_CHECK_TO_REGISTRY_CODE/
# _GATE_TAG_RE are duplicated from nova_langfuse_client.py rather than
# imported -- same "duplicated, not shared" precedent nova_langfuse_client.py
# itself already set for _GATE_TAG_RE ("kept as a local copy rather than a
# cross-module import for one regex").
#
# Gated behind framework_integrations.laminar_tracing (default off), same
# convention as every other integration in this codebase.
#
# Run standalone for a sanity check:
#   nova-env\\Scripts\\python nova_laminar_client.py
#   then verify with: npx lmnr-cli sql query "SELECT * FROM spans ORDER BY start_time DESC LIMIT 1" --json

import os
import re
from pathlib import Path

from dotenv import load_dotenv

from nova_config import is_framework_integration_enabled

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

LMNR_PROJECT_API_KEY = os.environ.get("LMNR_PROJECT_API_KEY")

_initialized = False

# ── Duplicated from nova_langfuse_client.py (see header comment for why) ──────
GUARD_TO_REGISTRY_CODE = {
    "read_before_write": "A1",
    "repeat_read": "A2",
    "repeat_failed_call": "B1",
    "write_file_nudge_missing_target": "B2",
    "write_file_nudge_threshold": "B3",
    "self_verify_nudge": "B4",
    "content_duplicate_function": "C1",
    "content_unreachable_code": "C2",
    "content_syntax_invalid": "C4",
    "file_allowlist": "D1",
    "goal_reanchor": "F2",
}

FINAL_STATUS_TO_REGISTRY_CODE = {
    "stopped_max_output_tokens": "E2",
}

GATE_CHECK_TO_REGISTRY_CODE = {
    "nonzero_diff": "E1",
    "required_files_touched": "E1",
    "deliverables_present": "E1",
    "unused_new_import": "E1",
    "syntax_valid": "C4",
    "module_level_name_order": "C4",
    "cross_module_circular_import": "C4",
    "cross_module_missing_export": "C4",
    "forbidden_paths_untouched": "D1",
    "narrow_scope_not_exceeded": "D1",
}

_GATE_TAG_RE = re.compile(r"^\[(\w+)\] ")


def _ensure_initialized() -> bool:
    """
    Laminar.initialize() sets up global SDK state once per process -- unlike
    Langfuse's per-call client object, there is no client instance to build
    and return here. Returns False (and never initializes) if the project
    API key isn't configured, so every caller can fail toward "tracing is
    off" the same way nova_langfuse_client.get_client() does.
    """
    global _initialized
    if not LMNR_PROJECT_API_KEY:
        return False
    if not _initialized:
        from lmnr import Laminar

        # Auto-instrumentation deliberately disabled: Nova's turn loops call
        # the Anthropic/RunPod APIs directly and this module already creates
        # its own manual spans via log_turn() with already-normalized data --
        # leaving auto-instrumentation on risks double-instrumenting the same
        # calls, exactly what the installed skill's own ground rules warn
        # against ("never run two tracer SDKs that both instrument the same
        # calls"). Also incidentally avoids a real wrap_function_wrapper()
        # compatibility error hit live from the threading auto-instrumentor
        # against this project's installed opentelemetry-instrumentation
        # version.
        Laminar.initialize(project_api_key=LMNR_PROJECT_API_KEY, instruments=set())
        _initialized = True
    return True


def verify_connectivity() -> bool:
    """
    Real, live proof the configured Laminar Cloud project is reachable --
    sends one real span and flushes it. Unlike Langfuse's get_trace_url(),
    Laminar's SDK has no confirmed way to hand back a ready-made per-trace
    URL from a plain span context, so verification here matches Laminar's
    own documented workflow: run this, then confirm the span landed via
    `lmnr-cli sql query` or the dashboard directly -- same "verify the
    payload, don't just trust no exception was raised" discipline as
    nova_langfuse_client.verify_connectivity(). Returns False if the
    project API key isn't configured.
    """
    if not _ensure_initialized():
        print("[nova_laminar_client] LMNR_PROJECT_API_KEY not set -- skipping")
        return False

    from lmnr import Laminar

    with Laminar.start_as_current_span(
        name="nova-laminar-connectivity-check",
        input="Sibling infra ticket sanity check (86bb7qudh)",
    ):
        Laminar.set_span_output("ok")

    Laminar.flush()
    print(
        "[nova_laminar_client] Sent a real span -- verify with "
        '`npx lmnr-cli sql query "SELECT * FROM spans ORDER BY start_time DESC LIMIT 1" --json` '
        "or the dashboard"
    )
    return True


def log_turn(
    branch_name: str,
    turn: int,
    backend_profile: str,
    model: str,
    content: str | None,
    tool_calls: list[dict],
    input_tokens: int | None,
    output_tokens: int | None,
    logprobs: list[dict] | None = None,
    cost_usd: float | None = None,
) -> None:
    """
    Mirrors nova_langfuse_client.log_turn()'s exact signature and calling
    convention -- called from the same three turn-loop call sites, additively,
    right alongside the existing Langfuse call and each backend's own
    agent_log.jsonl write. branch_name becomes this turn's Laminar session_id
    (see header comment) so every turn of one coding task groups together in
    the UI, the same intent as Langfuse's seeded trace_id.

    Fails open and NEVER raises -- same discipline as
    nova_langfuse_client.log_turn(). No-ops silently if laminar_tracing is
    off (the default) or the project API key isn't configured.
    """
    if not is_framework_integration_enabled("laminar_tracing"):
        return
    if not _ensure_initialized():
        return

    try:
        from lmnr import Attributes, Laminar

        logprob_mean = None
        logprob_min = None
        if logprobs:
            values = [entry["logprob"] for entry in logprobs]
            logprob_mean = sum(values) / len(values)
            logprob_min = min(values)

        with Laminar.start_as_current_span(
            name=f"turn-{turn}",
            span_type="LLM",
            input=content,
        ):
            Laminar.set_trace_session_id(branch_name)
            Laminar.set_span_output({"tool_calls": tool_calls} if tool_calls else None)

            attributes = {
                Attributes.PROVIDER: backend_profile,
                Attributes.REQUEST_MODEL: model,
                Attributes.RESPONSE_MODEL: model,
            }
            if input_tokens is not None:
                attributes[Attributes.INPUT_TOKEN_COUNT] = input_tokens
            if output_tokens is not None:
                attributes[Attributes.OUTPUT_TOKEN_COUNT] = output_tokens
            Laminar.set_span_attributes(attributes)

            Laminar.set_trace_metadata(
                {
                    "backend_profile": backend_profile,
                    "branch": branch_name,
                    "tool_call_count": len(tool_calls),
                    "logprob_mean": logprob_mean,
                    "logprob_min": logprob_min,
                    "cost_usd": cost_usd,
                }
            )

        Laminar.flush()
    except Exception as e:
        # Broad except deliberately -- see docstring's fail-open discipline.
        print(f"[nova_laminar_client] log_turn() failed, continuing without tracing: {e}")


def log_guard_events(branch_name: str, final_status: str, guard_events: list[dict]) -> None:
    """
    Mirrors nova_langfuse_client.log_guard_events()'s calling convention and
    registry-code mapping exactly, called from the same _log_guard_events()
    call site additively. Logs one span per call carrying every registry
    code from this run (not one score per guard event, unlike Langfuse) --
    see this file's header comment for why. Queryable via the SQL API's
    `spans` table.

    Fails open and NEVER raises. No-ops silently if laminar_tracing is off
    or the project API key isn't configured.
    """
    if not is_framework_integration_enabled("laminar_tracing"):
        return
    if not _ensure_initialized():
        return

    try:
        from lmnr import Laminar

        registry_codes = [
            GUARD_TO_REGISTRY_CODE.get(event.get("guard", ""), f"unmapped:{event.get('guard', '')}")
            for event in guard_events
        ]
        status_code = FINAL_STATUS_TO_REGISTRY_CODE.get(final_status)
        if status_code:
            registry_codes.append(status_code)

        with Laminar.start_as_current_span(name="guard_events", input=guard_events):
            Laminar.set_trace_session_id(branch_name)
            Laminar.set_span_output({"registry_codes": registry_codes, "final_status": final_status})
            Laminar.set_trace_metadata({"branch": branch_name, "registry_codes": registry_codes})

        Laminar.flush()
    except Exception as e:
        # Broad except deliberately -- see docstring's fail-open discipline.
        print(f"[nova_laminar_client] log_guard_events() failed, continuing without tracing: {e}")


def log_gate_result(branch_name: str, gate_result: dict) -> None:
    """
    Mirrors nova_langfuse_client.log_gate_result()'s calling convention and
    registry-code mapping exactly. Same span-based approach as
    log_guard_events() -- see this file's header comment for why.

    Fails open and NEVER raises. No-ops silently if laminar_tracing is off
    or the project API key isn't configured.
    """
    if not is_framework_integration_enabled("laminar_tracing"):
        return
    if not _ensure_initialized():
        return

    try:
        from lmnr import Laminar

        tagged = []
        for kind, messages in (
            ("gate_hard_fail", gate_result.get("hard_fails", [])),
            ("gate_warning", gate_result.get("warnings", [])),
        ):
            for message in messages:
                match = _GATE_TAG_RE.match(message)
                check_name = match.group(1) if match else "(untagged)"
                registry_code = GATE_CHECK_TO_REGISTRY_CODE.get(check_name, f"unmapped:{check_name}")
                tagged.append(
                    {"kind": kind, "check_name": check_name, "registry_code": registry_code, "message": message}
                )

        gate_passed = bool(gate_result.get("passed", False))
        with Laminar.start_as_current_span(name="gate_result", input=gate_result):
            Laminar.set_trace_session_id(branch_name)
            Laminar.set_span_output({"tagged": tagged, "passed": gate_passed})
            Laminar.set_trace_metadata({"branch": branch_name, "gate_passed": gate_passed})

        Laminar.flush()
    except Exception as e:
        # Broad except deliberately -- see docstring's fail-open discipline.
        print(f"[nova_laminar_client] log_gate_result() failed, continuing without tracing: {e}")


if __name__ == "__main__":
    ok = verify_connectivity()
    if not ok:
        print("[nova_laminar_client] Connectivity check did not complete -- see messages above.")
