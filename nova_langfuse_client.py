# nova_langfuse_client.py
# Langfuse Cloud connectivity for Nova's Observability Initiative (86bb7pamh),
# Phase 0 (86bb7par3).
#
# Deliberately connectivity-verification ONLY -- reads credentials, builds a
# client, and proves a real trace reaches the dashboard. The real turn-loop
# instrumentation (reasoning/tool-calls/token-uncertainty wired into BOTH
# nova_orchestrator.run_coding_task() AND nova_coding_eval.py's eval path,
# per the G2 lesson -- a mechanism that only fires from one call site isn't
# really built) is Phase 1's job, deliberately not started here.
#
# Cloud, not self-hosted: Langfuse v3's official self-hosted stack (Postgres/
# ClickHouse/Redis/MinIO/web/worker, 6 containers) recommends 4 vCPU/16GB RAM
# for VM deployments -- the Omen has 7.64GB RAM total and already runs real
# production services (nova-api, nova-chroma). Langfuse v2 (lighter, no
# ClickHouse) was checked and ruled out: unmaintained since end of Q1 2025,
# a bad tradeoff for RAM savings on an internet-adjacent box. Decided with
# Marvin (2026-08-03): Langfuse Cloud free tier instead -- a deliberate,
# acknowledged exception to Nova's usual local-first default (trace content
# leaves the local network), made knowingly, not by accident.
#
# Run standalone for a sanity check:
#   nova-env\\Scripts\\python nova_langfuse_client.py

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from langfuse import Langfuse

from nova_config import is_framework_integration_enabled

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY")
# LANGFUSE_BASE_URL, not LANGFUSE_HOST -- confirmed directly against the
# installed SDK's own Langfuse.__init__() source: base_url/LANGFUSE_BASE_URL
# take precedence over host/LANGFUSE_HOST in its real env-var resolution
# order, and LANGFUSE_BASE_URL is what a real Langfuse Cloud project's own
# "get API keys" page hands you to copy-paste.
LANGFUSE_BASE_URL = os.environ.get("LANGFUSE_BASE_URL")


def get_client() -> Langfuse | None:
    """
    Build a Langfuse client from this repo's own explicit env vars (same
    discipline as every other credential in this codebase -- read once via
    os.environ.get(), not implicit env-var auto-detection). Returns None if
    any of the three required values is missing, so a caller can fail
    toward "tracing is off" rather than crash -- Phase 1's real
    instrumentation will need this same fail-open discipline once it's
    wired into the production turn loop.
    """
    if not (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY and LANGFUSE_BASE_URL):
        return None
    return Langfuse(public_key=LANGFUSE_PUBLIC_KEY, secret_key=LANGFUSE_SECRET_KEY, base_url=LANGFUSE_BASE_URL)


def verify_connectivity() -> str | None:
    """
    Real, live proof the configured Langfuse Cloud project is reachable --
    not just "no exception was raised." auth_check() confirms the key pair
    is valid; a real observation is then created, flushed, and its trace
    URL returned, so this can be opened in a browser and visually confirmed
    rather than trusted from a boolean alone (same "verify the payload, not
    just the status code" discipline as CLAUDE.md's Omen deployment lesson).
    Returns None if credentials are missing.
    """
    client = get_client()
    if client is None:
        print("[nova_langfuse_client] LANGFUSE_PUBLIC_KEY/SECRET_KEY/BASE_URL not fully set -- skipping")
        return None

    client.auth_check()
    print("[nova_langfuse_client] auth_check() passed -- credentials are valid")

    with client.start_as_current_observation(
        name="nova-phase-0-connectivity-check",
        as_type="span",
        input="Phase 0 sanity check (86bb7par3)",
        output="ok",
    ):
        trace_url = client.get_trace_url()

    client.flush()
    return trace_url


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
    One shared turn-logging path for Observability Initiative Phase 1
    (86bb7pawp), called from all three real coding-agent turn loops
    (nova_orchestrator.run_coding_task(), nova_orchestrator_runpod.
    run_via_runpod(), nova_orchestrator_devstral.run_via_devstral()) right
    alongside each backend's existing agent_log.jsonl write -- additive, not
    a replacement (agent_log.jsonl stays the source nova_coding_dataset_
    curator.py/nova_guard_stats.py already read).

    Each backend normalizes its own response shape to these plain
    primitives ONCE, so this function doesn't need to know about Anthropic
    SDK objects, RunPod envelopes, or OpenAI-style tool_calls dicts --
    exactly the "normalize once, log once" reason this function exists
    instead of three separate ad-hoc Langfuse integrations.

    branch_name is used as create_trace_id()'s deterministic seed, so every
    turn of one coding task (already uniquely identified by its own
    disposable git branch) nests under a single real Langfuse trace instead
    of one fragmented trace per turn -- what makes a later per-task
    dashboard (Phase 3) meaningful.

    logprobs (real per-token {"token", "logprob"} pairs, or None -- Claude's
    API exposes no such data, so the Claude lane always passes None here)
    is reduced to a mean and a min before being sent, both stored in
    metadata rather than as a separate numeric field Langfuse has no native
    slot for. Mean answers "was this response confident overall"; min
    answers "was there one point of real uncertainty" -- a single low-
    confidence token (e.g. a guessed variable name) can sit inside an
    otherwise-fluent response and get averaged away by the mean alone.

    Fails open and NEVER raises: a tracing failure (network, bad creds,
    SDK error) must never break a real coding-agent turn -- same discipline
    as _log_guard_events()/_log_runpod_cost_summary() elsewhere in this
    codebase. No-ops silently if langfuse_tracing is off (the default) or
    credentials aren't configured, without even trying a network call.
    """
    if not is_framework_integration_enabled("langfuse_tracing"):
        return

    client = get_client()
    if client is None:
        return

    try:
        trace_id = client.create_trace_id(seed=branch_name)

        logprob_mean = None
        logprob_min = None
        if logprobs:
            values = [entry["logprob"] for entry in logprobs]
            logprob_mean = sum(values) / len(values)
            logprob_min = min(values)

        usage_details = {}
        if input_tokens is not None:
            usage_details["input"] = input_tokens
        if output_tokens is not None:
            usage_details["output"] = output_tokens

        with client.start_as_current_observation(
            name=f"turn-{turn}",
            as_type="generation",
            trace_context={"trace_id": trace_id},
            input=content,
            output={"tool_calls": tool_calls} if tool_calls else None,
            model=model,
            usage_details=usage_details or None,
            cost_details={"total": cost_usd} if cost_usd is not None else None,
            metadata={
                "backend_profile": backend_profile,
                "branch": branch_name,
                "tool_call_count": len(tool_calls),
                "logprob_mean": logprob_mean,
                "logprob_min": logprob_min,
            },
        ):
            pass

        client.flush()
    except Exception as e:
        # Broad except deliberately -- see docstring's fail-open discipline.
        print(f"[nova_langfuse_client] log_turn() failed, continuing without tracing: {e}")


# ── Observability Phase 2 (86bb7pazm) -- guard/gate signal -> failure registry ──
#
# Maps the coding sub-agent's existing guard/gate identifiers onto the real
# 19-entry A1-G2 failure-mode taxonomy (reference_coding_agent_failure_
# registry.md). Explicitly NOT new detection logic -- every signal mapped
# here already fires and already lands in guard_events_log.jsonl/ground_
# truth_gate_log.jsonl; this only makes it queryable in Langfuse against a
# real failure category instead of requiring a hand grep across JSONL files.
#
# Codes with no current guard/check at all (C3, C5, D2, F1) simply never
# get a score here -- correct behavior, not a gap (future guard-engineering
# work, out of Phase 2's scope). G1/G2 are meta/harness-level and already
# resolved; nothing to tag per-task.

GUARD_TO_REGISTRY_CODE = {
    "read_before_write": "A1",
    "repeat_read": "A2",
    "repeat_failed_call": "B1",
    "write_file_nudge_missing_target": "B2",  # the "wrong tool, redirect" nudge
    "write_file_nudge_threshold": "B3",
    "self_verify_nudge": "B4",
    "content_duplicate_function": "C1",
    "content_unreachable_code": "C2",
    "content_syntax_invalid": "C4",
    "file_allowlist": "D1",  # partial signal -- see D1's own registry note
    "goal_reanchor": "F2",
}
# near_miss_parse deliberately unmapped: a tool-call-FORMAT parsing artifact
# of the prompted <tools> backend, not one of the A-G model-behavior failure
# modes.

FINAL_STATUS_TO_REGISTRY_CODE = {
    "stopped_max_output_tokens": "E2",
}

GATE_CHECK_TO_REGISTRY_CODE = {
    "nonzero_diff": "E1",
    "required_files_touched": "E1",
    "deliverables_present": "E1",
    "unused_new_import": "E1",  # built specifically for E1's "half-done, one trace left" shape
    "syntax_valid": "C4",
    "module_level_name_order": "C4",  # NameError-at-import-time; closest existing slot to "won't import"
    "cross_module_circular_import": "C4",
    "cross_module_missing_export": "C4",
    "forbidden_paths_untouched": "D1",  # built directly from D1's own registry "worth considering" note
    "narrow_scope_not_exceeded": "D1",  # the specific check that WOULD catch the real D1 incident
}

# nova_completion_gate._tag()'s exact format: "[check_name] rest of message"
# -- same pattern nova_guard_stats.py's own _GATE_TAG_RE already uses. Kept
# as a local copy rather than a cross-module import for one regex.
_GATE_TAG_RE = re.compile(r"^\[(\w+)\] ")


def log_guard_events(branch_name: str, final_status: str, guard_events: list[dict]) -> None:
    """
    Tag each guard/nudge firing from one coding-agent run onto that task's
    Langfuse trace as a "guard_fire" score, keyed to the real A1-G2 failure
    registry code (or "unmapped:<guard_id>" for a guard with no registry
    slot yet). Also emits one more guard_fire score if final_status itself
    maps via FINAL_STATUS_TO_REGISTRY_CODE (covers E2, which never shows up
    inside guard_events since it's a whole-run outcome, not a per-turn
    nudge).

    Called from _log_guard_events() right alongside its existing
    guard_events_log.jsonl write -- additive only, that JSONL file stays
    the source nova_guard_stats.py already reads.

    Fails open and NEVER raises -- same discipline as log_turn(). No-ops
    silently if langfuse_tracing is off or credentials aren't configured.
    """
    if not is_framework_integration_enabled("langfuse_tracing"):
        return

    client = get_client()
    if client is None:
        return

    try:
        trace_id = client.create_trace_id(seed=branch_name)

        for event in guard_events:
            guard_id = event.get("guard", "")
            detail = event.get("detail", "")
            registry_code = GUARD_TO_REGISTRY_CODE.get(guard_id, f"unmapped:{guard_id}")
            client.create_score(
                name="guard_fire",
                value=registry_code,
                trace_id=trace_id,
                data_type="CATEGORICAL",
                comment=f"{guard_id}: {detail}",
                metadata={"guard_id": guard_id, "detail": detail},
            )

        status_code = FINAL_STATUS_TO_REGISTRY_CODE.get(final_status)
        if status_code:
            client.create_score(
                name="guard_fire",
                value=status_code,
                trace_id=trace_id,
                data_type="CATEGORICAL",
                comment=f"final_status: {final_status}",
                metadata={"guard_id": final_status, "detail": "final_status"},
            )

        client.flush()
    except Exception as e:
        # Broad except deliberately -- see docstring's fail-open discipline.
        print(f"[nova_langfuse_client] log_guard_events() failed, continuing without tracing: {e}")


def log_gate_result(branch_name: str, gate_result: dict) -> None:
    """
    Tag one ground-truth completion gate result onto that task's Langfuse
    trace: one "gate_hard_fail"/"gate_warning" score per tagged message
    (parsed via _GATE_TAG_RE, keyed to the real A1-G2 failure registry code
    via GATE_CHECK_TO_REGISTRY_CODE, or "unmapped:<check_name>" for a check
    with no registry slot yet), plus one summary "gate_passed" boolean
    score.

    Called from _log_ground_truth_gate() right alongside its existing
    ground_truth_gate_log.jsonl write -- additive only, that JSONL file
    stays the source of record.

    Fails open and NEVER raises -- same discipline as log_turn(). No-ops
    silently if langfuse_tracing is off or credentials aren't configured.
    """
    if not is_framework_integration_enabled("langfuse_tracing"):
        return

    client = get_client()
    if client is None:
        return

    try:
        trace_id = client.create_trace_id(seed=branch_name)

        for score_name, messages in (
            ("gate_hard_fail", gate_result.get("hard_fails", [])),
            ("gate_warning", gate_result.get("warnings", [])),
        ):
            for message in messages:
                match = _GATE_TAG_RE.match(message)
                check_name = match.group(1) if match else "(untagged)"
                registry_code = GATE_CHECK_TO_REGISTRY_CODE.get(check_name, f"unmapped:{check_name}")
                client.create_score(
                    name=score_name,
                    value=registry_code,
                    trace_id=trace_id,
                    data_type="CATEGORICAL",
                    comment=message,
                    metadata={"check_name": check_name},
                )

        client.create_score(
            name="gate_passed",
            value=bool(gate_result.get("passed", False)),
            trace_id=trace_id,
            data_type="BOOLEAN",
        )

        client.flush()
    except Exception as e:
        # Broad except deliberately -- see docstring's fail-open discipline.
        print(f"[nova_langfuse_client] log_gate_result() failed, continuing without tracing: {e}")


if __name__ == "__main__":
    url = verify_connectivity()
    if url:
        print(f"[nova_langfuse_client] Real trace sent -- view it here: {url}")
    else:
        print("[nova_langfuse_client] Connectivity check did not complete -- see messages above.")
