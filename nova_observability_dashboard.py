# nova_observability_dashboard.py
# Data layer for Observability Initiative Phase 3 (86bb7pb20) — trend
# dashboards on top of Phase 0-2's Langfuse tracing + guard/gate signal.
#
# Three sections, matching the ticket's exact scope:
#   1. failure_frequency_over_time() — registry-code fires by date, from
#      local JSONL (guard_events_log.jsonl + ground_truth_gate_log.jsonl).
#   2. per_model_comparison() — same local JSONL, joined to agent_log.jsonl's
#      model field via branch.
#   3. uncertainty_vs_outcome() — Langfuse Cloud only. Per-token logprob
#      data is never persisted locally (agent_log.jsonl has no logprob
#      field — only log_turn()'s Langfuse metadata carries it).
#
# Local JSONL is the richer source for #1/#2: it's written unconditionally
# by every real coding-agent run, regardless of whether the langfuse_tracing
# flag was on. This module deliberately reuses nova_guard_stats.py's
# parsing primitives and nova_langfuse_client.py's registry-code mapping
# dicts rather than redefining either — see each import below.
#
# Deliberately does NOT import nova_coding_eval.py for its existing
# branch->agent_log helper: that module transitively pulls in
# nova_orchestrator/nova_orchestrator_runpod/nova_orchestrator_devstral
# (the Claude API client + RunPod/Devstral inference clients) for what
# should be a lightweight read-only lookup. This file declares its own
# tiny AGENT_LOG_PATH/TASK_OUTCOMES_LOG_PATH constants instead.

import datetime
import os

from nova_guard_stats import _GATE_TAG_RE, LOGS_DIR, _included, _parse_jsonl
from nova_langfuse_client import (
    FINAL_STATUS_TO_REGISTRY_CODE,
    GATE_CHECK_TO_REGISTRY_CODE,
    GUARD_TO_REGISTRY_CODE,
    get_client,
)
from nova_observability_status import get_combined_observability_data

AGENT_LOG_PATH = os.path.join(LOGS_DIR, "agent_log.jsonl")
TASK_OUTCOMES_LOG_PATH = os.path.join(LOGS_DIR, "agent_task_outcomes.jsonl")

# Outcome-bucket classification constants — see classify_outcome_bucket()'s
# own docstring for the full reasoning behind each one.
NOT_ATTEMPTED_CHECK_NAME = "nonzero_diff"
SEVERE_GATE_REGISTRY_CODES = {"D1", "C4"}  # scope violation / won't-even-import
INFRA_HALT_STATUSES = {"stopped_context_overflow", "stopped_runpod_call_failed"}
CLEAN_FINAL_STATUS = "completed"

UNCERTAINTY_DEFAULT_WINDOW_DAYS = 30
UNCERTAINTY_PAGE_SIZE = 100
# Caps a single call at 500 real traces -- generous relative to how little
# real dispatch traffic has been traced so far (langfuse_tracing defaults
# off), while still bounding the loop instead of paginating unconditionally.
UNCERTAINTY_MAX_PAGES = 5


def _date_bucket(timestamp: str) -> str:
    """First 10 characters of an ISO timestamp ("YYYY-MM-DD"). Every real log's timestamp is already isoformat()."""
    return timestamp[:10] if timestamp else "(unknown date)"


def _latest_entry_by_branch(entries: list[dict]) -> dict[str, dict]:
    """
    Most recent entry per branch, keyed by its own timestamp field. A branch
    can appear more than once across retries of the same task — later
    real-world runs supersede earlier ones for classification purposes.
    """
    latest: dict[str, dict] = {}
    for entry in entries:
        branch = entry.get("branch")
        if not branch:
            continue
        if branch not in latest or entry.get("timestamp", "") >= latest[branch].get("timestamp", ""):
            latest[branch] = entry
    return latest


def _branch_to_model_map(agent_log_entries: list[dict]) -> dict[str, str]:
    """
    Each branch's model, from its first logged turn in the given agent_log
    entries — a branch's backend never changes mid-run, so the first turn
    is enough. Takes entries as a parameter (rather than reading
    AGENT_LOG_PATH itself) so callers can pass in cross-machine combined
    data (nova_observability_status.get_combined_observability_data()) —
    real bug found 2026-08-06: the Omen's own agent_log.jsonl has only 137
    lines vs. the Aero's 4,765, since almost all real coding-agent activity
    happens on the Aero, so a local-only read here silently missed most of
    it whenever served from the Omen.
    """
    mapping: dict[str, str] = {}
    for entry in agent_log_entries:
        branch = entry.get("branch")
        if branch and branch not in mapping:
            mapping[branch] = entry.get("model") or "(unknown model)"
    return mapping


def _task_outcomes_by_branch() -> dict[str, dict]:
    """
    branch -> its agent_task_outcomes.jsonl entry (merged/discarded/budget_halt).
    Sparse -- not every branch has one.
    """
    outcomes: dict[str, dict] = {}
    for entry in _parse_jsonl(TASK_OUTCOMES_LOG_PATH):
        branch = entry.get("branch")
        if branch:
            outcomes[branch] = entry
    return outcomes


def failure_frequency_over_time(branch_prefix: str | None = None) -> dict:
    """
    Real A1-G2 registry-code fires, bucketed by date, from
    guard_events_log.jsonl and ground_truth_gate_log.jsonl — cross-machine
    combined (nova_observability_status.get_combined_observability_data()),
    not local-only. Real bug found 2026-08-06: both files don't even exist
    on the Omen, so a route served from there was silently showing nothing.
    Maps each real firing to its registry code via the same dicts Phase 2
    already built (GUARD_TO_REGISTRY_CODE / GATE_CHECK_TO_REGISTRY_CODE /
    FINAL_STATUS_TO_REGISTRY_CODE) — not new detection logic, just a time
    dimension on top of signal that already exists.

    Returns {"dates": [...], "codes": [...], "series": {code: [count_per_date, ...]},
    "unmapped_count": N, "view": "combined"|"omen_only"|"aero_only"}.
    """
    combined = get_combined_observability_data()
    guard_entries = [e for e in combined["guard_entries"] if _included(e.get("branch", ""), branch_prefix, None)]
    gate_entries = [e for e in combined["gate_entries"] if _included(e.get("branch", ""), branch_prefix, None)]

    counts: dict[str, dict[str, int]] = {}
    unmapped_count = 0

    def _bump(date: str, code: str) -> None:
        day = counts.setdefault(date, {})
        day[code] = day.get(code, 0) + 1

    for entry in guard_entries:
        date = _date_bucket(entry.get("timestamp", ""))
        for event in entry.get("guard_events", []):
            code = GUARD_TO_REGISTRY_CODE.get(event.get("guard", ""))
            if code is None:
                unmapped_count += 1
                continue
            _bump(date, code)
        status_code = FINAL_STATUS_TO_REGISTRY_CODE.get(entry.get("final_status"))
        if status_code:
            _bump(date, status_code)

    for entry in gate_entries:
        date = _date_bucket(entry.get("timestamp", ""))
        for message in entry.get("hard_fails", []) + entry.get("warnings", []):
            match = _GATE_TAG_RE.match(message)
            code = GATE_CHECK_TO_REGISTRY_CODE.get(match.group(1)) if match else None
            if code is None:
                unmapped_count += 1
                continue
            _bump(date, code)

    dates = sorted(counts.keys())
    codes = sorted({code for day_counts in counts.values() for code in day_counts})
    series = {code: [counts.get(date, {}).get(code, 0) for date in dates] for code in codes}
    return {
        "dates": dates,
        "codes": codes,
        "series": series,
        "unmapped_count": unmapped_count,
        "view": combined["view"],
    }


def classify_outcome_bucket(gate_entry: dict | None, guard_entry: dict | None, task_outcome: dict | None) -> str:
    """
    One of unknown / not_attempted / catastrophic / clean_completion /
    partial, evaluated in that priority order. The first three are the
    ticket's own named buckets (86bb7pb20); "partial" is a genuine 4th
    bucket for a real case in the actual logs — gate passed but the run
    itself didn't cleanly finish (e.g. max_turns_reached) — that forcing
    into clean_completion would overstate.

    not_attempted checks the RAW parsed check_name ("nonzero_diff"), not
    the broader mapped "E1" registry code — E1 also covers
    required_files_touched/deliverables_present/unused_new_import, which
    mean "attempted but incomplete," not "nothing happened."
    """
    if gate_entry is None:
        return "unknown"

    passed = gate_entry.get("passed", False)
    hard_fail_checks = []
    for message in gate_entry.get("hard_fails", []):
        match = _GATE_TAG_RE.match(message)
        if match:
            hard_fail_checks.append(match.group(1))

    if not passed and NOT_ATTEMPTED_CHECK_NAME in hard_fail_checks:
        return "not_attempted"

    hard_fail_codes = {GATE_CHECK_TO_REGISTRY_CODE.get(name) for name in hard_fail_checks}
    final_status = guard_entry.get("final_status") if guard_entry else None
    outcome = task_outcome.get("outcome") if task_outcome else None

    if not passed and (
        hard_fail_codes & SEVERE_GATE_REGISTRY_CODES or outcome == "budget_halt" or final_status in INFRA_HALT_STATUSES
    ):
        return "catastrophic"

    if passed and final_status == CLEAN_FINAL_STATUS:
        return "clean_completion"

    return "partial"


def classify_outcomes_for_all_branches(gate_entries: list[dict], guard_entries: list[dict]) -> dict[str, str]:
    """
    branch -> its classify_outcome_bucket() result, for every branch with at
    least a gate or guard entry. Takes already-fetched, already-filtered
    entries as parameters (rather than reading the JSONL files itself) so
    a caller that already fetched cross-machine combined data
    (nova_observability_status.get_combined_observability_data()) doesn't
    trigger a second, redundant SSH round-trip just to classify outcomes.
    """
    gate_by_branch = _latest_entry_by_branch(gate_entries)
    guard_by_branch = _latest_entry_by_branch(guard_entries)
    outcomes_by_branch = _task_outcomes_by_branch()

    all_branches = set(gate_by_branch) | set(guard_by_branch)
    return {
        branch: classify_outcome_bucket(
            gate_by_branch.get(branch),
            guard_by_branch.get(branch),
            outcomes_by_branch.get(branch),
        )
        for branch in all_branches
    }


def per_model_comparison(branch_prefix: str | None = None) -> dict:
    """
    Per-model breakdown: run count, gate pass rate, registry-code fire
    counts, and outcome-bucket counts. Joins guard/gate log entries to
    agent_log.jsonl's model field via branch, since guard/gate entries
    never carry model directly. A branch with no matching agent_log.jsonl
    entry lands under "(unknown model)" rather than being dropped.

    Cross-machine combined (nova_observability_status.
    get_combined_observability_data()), not local-only — real bug found
    2026-08-06: guard_events_log.jsonl/ground_truth_gate_log.jsonl don't
    exist on the Omen at all, and its agent_log.jsonl has only 137 lines
    vs. the Aero's 4,765, so this route was silently returning {"models":
    {}} whenever served from the Omen. Fetched once here and passed down
    to _branch_to_model_map()/classify_outcomes_for_all_branches() rather
    than each independently re-fetching (one SSH round-trip, not three).

    Returns {"models": {model: {"runs": N, "gate_passed": N, "gate_total": N,
    "gate_pass_rate": float | None, "registry_code_fires": {code: N},
    "outcome_buckets": {bucket: N}}}, "view": "combined"|"omen_only"|"aero_only"}.
    """
    combined = get_combined_observability_data()
    gate_entries = [e for e in combined["gate_entries"] if _included(e.get("branch", ""), branch_prefix, None)]
    guard_entries = [e for e in combined["guard_entries"] if _included(e.get("branch", ""), branch_prefix, None)]

    branch_model = _branch_to_model_map(combined["agent_log_entries"])
    outcome_by_branch = classify_outcomes_for_all_branches(gate_entries, guard_entries)

    gate_by_branch = _latest_entry_by_branch(gate_entries)
    guard_by_branch = _latest_entry_by_branch(guard_entries)

    all_branches = set(gate_by_branch) | set(guard_by_branch)

    models: dict[str, dict] = {}
    for branch in all_branches:
        model = branch_model.get(branch, "(unknown model)")
        bucket = models.setdefault(
            model,
            {"runs": 0, "gate_passed": 0, "gate_total": 0, "registry_code_fires": {}, "outcome_buckets": {}},
        )
        bucket["runs"] += 1

        gate_entry = gate_by_branch.get(branch)
        if gate_entry is not None:
            bucket["gate_total"] += 1
            if gate_entry.get("passed"):
                bucket["gate_passed"] += 1
            for message in gate_entry.get("hard_fails", []) + gate_entry.get("warnings", []):
                match = _GATE_TAG_RE.match(message)
                code = GATE_CHECK_TO_REGISTRY_CODE.get(match.group(1)) if match else None
                if code:
                    bucket["registry_code_fires"][code] = bucket["registry_code_fires"].get(code, 0) + 1

        guard_entry = guard_by_branch.get(branch)
        if guard_entry is not None:
            for event in guard_entry.get("guard_events", []):
                code = GUARD_TO_REGISTRY_CODE.get(event.get("guard", ""))
                if code:
                    bucket["registry_code_fires"][code] = bucket["registry_code_fires"].get(code, 0) + 1

        outcome_bucket_name = outcome_by_branch.get(branch, "unknown")
        bucket["outcome_buckets"][outcome_bucket_name] = bucket["outcome_buckets"].get(outcome_bucket_name, 0) + 1

    for model_stats in models.values():
        model_stats["gate_pass_rate"] = (
            model_stats["gate_passed"] / model_stats["gate_total"] if model_stats["gate_total"] else None
        )

    return {"models": models, "view": combined["view"]}


def uncertainty_vs_outcome(days: int = UNCERTAINTY_DEFAULT_WINDOW_DAYS) -> dict:
    """
    Real per-token uncertainty (Langfuse-only — agent_log.jsonl never
    persists logprobs locally) cross-referenced against each generation's
    real outcome bucket. The outcome bucket is computed via
    classify_outcomes_for_all_branches() over cross-machine combined gate/
    guard data (nova_observability_status.get_combined_observability_data(),
    real bug found 2026-08-06 — see per_model_comparison()'s own docstring),
    not from Langfuse's own guard_fire/gate_passed scores — local gate/guard
    JSONL is the more complete source, available for every real run
    regardless of whether langfuse_tracing happened to be on for it.

    Gates on credentials being configured, NOT on the langfuse_tracing
    write-flag — that flag only governs whether NEW traces get written.
    Real historical trace data (e.g. from Phase 1/2's verification runs)
    is already worth reading back even with the flag off today.

    Fails open and NEVER raises: any Langfuse read failure (network, auth,
    SDK schema drift) returns an explanatory available:false payload
    instead of raising — same discipline as nova_langfuse_client.py's
    write-side functions.
    """
    client = get_client()
    if client is None:
        return {"available": False, "reason": "Langfuse credentials not configured", "points": []}

    try:
        combined = get_combined_observability_data()
        outcome_by_branch = classify_outcomes_for_all_branches(combined["gate_entries"], combined["guard_entries"])
        cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)

        points = []
        page = 1
        while page <= UNCERTAINTY_MAX_PAGES:
            result = client.api.trace.list(from_timestamp=cutoff, limit=UNCERTAINTY_PAGE_SIZE, page=page)
            for trace_summary in result.data:
                full_trace = client.api.trace.get(trace_summary.id)
                for obs in full_trace.observations:
                    if obs.type != "GENERATION":
                        continue
                    metadata = obs.metadata or {}
                    logprob_mean = metadata.get("logprob_mean")
                    if logprob_mean is None:
                        continue
                    branch = metadata.get("branch", "(unknown branch)")
                    points.append(
                        {
                            "branch": branch,
                            "turn": obs.name,
                            "logprob_mean": logprob_mean,
                            "logprob_min": metadata.get("logprob_min"),
                            "backend_profile": metadata.get("backend_profile"),
                            "outcome_bucket": outcome_by_branch.get(branch, "unknown"),
                        }
                    )
            if page >= result.meta.total_pages:
                break
            page += 1

        return {"available": True, "points": points}
    except Exception as e:
        return {"available": False, "reason": str(e), "points": []}
