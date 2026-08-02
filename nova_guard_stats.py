# nova_guard_stats.py
# Guard-firing attribution for the RunPod coding-agent lane (Marvin's ask,
# 2026-08-02, following the Qwen2.5-Coder-32B spike's 86bb4gy0y punch list).
#
# Every guard/nudge shipped into nova_orchestrator_runpod.py this week
# (read-before-write, repeat-read, repeat-failed-call, the task-scoped file
# allowlist, the post-dispatch syntax/duplicate-function check, near-miss
# tool-call parsing, goal re-anchoring, the self-verification nudge, and the
# two write_file fallback nudges) plus nova_completion_gate.py's 6 named
# checks now tag every real firing with a stable id (see
# nova_orchestrator_runpod.py's GUARD_* constants and nova_completion_gate.
# _tag()). Before this, a re-run's report told you pass/fail but not which
# specific fix actually caught something -- every iteration decision this
# week needed a fresh manual diff-by-diff read to reconstruct that. This
# script reads what's already logged and answers it directly: which guards
# are pulling their weight, across however many runs have happened so far.
#
# Deliberately read-only, no new log format -- reads
# logs/guard_events_log.jsonl (nova_orchestrator_runpod._log_guard_events(),
# one entry per task run) and logs/ground_truth_gate_log.jsonl
# (nova_orchestrator._log_ground_truth_gate(), same one-entry-per-task-run
# shape, already tagged via nova_completion_gate._tag()'s "[check_name] ..."
# prefix). Matches the existing small-status-script pattern
# (nova_agent_log_status.py, nova_training_data_status.py,
# nova_worktree_status.py) rather than inventing a new one.

import argparse
import json
import os
import re
import sys

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
GUARD_EVENTS_LOG_PATH = os.path.join(LOGS_DIR, "guard_events_log.jsonl")
GROUND_TRUTH_GATE_LOG_PATH = os.path.join(LOGS_DIR, "ground_truth_gate_log.jsonl")

# nova_completion_gate._tag()'s exact format: "[check_name] rest of message".
_GATE_TAG_RE = re.compile(r"^\[(\w+)\] ")


def _parse_jsonl(path: str) -> list[dict]:
    """Read a JSONL file into a list of dicts, silently skipping blank/malformed lines. [] if missing."""
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return entries


def _included(branch: str, branch_prefix: str | None, branches: set[str] | None) -> bool:
    """
    A run is in scope when it passes BOTH filters that were actually given
    (either left None means "don't restrict on this axis"). branch_prefix is
    the convenient CLI-facing filter (e.g. "nova-agent/"); branches is exact
    -match, for a programmatic caller (nova_coding_eval.py) that already
    knows precisely which branch names belong to one eval run and wants no
    ambiguity from a shared prefix.
    """
    if branch_prefix is not None and not branch.startswith(branch_prefix):
        return False
    if branches is not None and branch not in branches:
        return False
    return True


def _tally_guard_events(entries: list[dict], branch_prefix: str | None, branches: set[str] | None) -> dict:
    """
    {guard_id: {"fires": N, "runs_fired_in": M}} from guard_events_log.jsonl
    entries, plus how many runs were considered at all. "fires" counts every
    individual occurrence (a guard can fire more than once in one task run);
    "runs_fired_in" counts distinct task runs it showed up in at least
    once -- the more useful number for "does this guard matter," since a
    guard firing 10 times in one pathological run looks very different from
    firing once each across 10 separate runs.
    """
    checks: dict = {}
    runs_considered = 0
    for entry in entries:
        if not _included(entry.get("branch", ""), branch_prefix, branches):
            continue
        runs_considered += 1
        fired_this_run = set()
        for event in entry.get("guard_events", []):
            guard_id = event.get("guard", "(unknown)")
            bucket = checks.setdefault(guard_id, {"fires": 0, "runs_fired_in": 0})
            bucket["fires"] += 1
            fired_this_run.add(guard_id)
        for guard_id in fired_this_run:
            checks[guard_id]["runs_fired_in"] += 1
    return {"total_runs": runs_considered, "checks": checks}


def _tally_gate_checks(entries: list[dict], branch_prefix: str | None, branches: set[str] | None) -> dict:
    """
    Same shape as _tally_guard_events(), but sourced from
    ground_truth_gate_log.jsonl's hard_fails/warnings lists, parsing the
    "[check_name] ..." prefix nova_completion_gate._tag() adds. An entry
    logged before that tagging shipped (no prefix) falls under
    "(untagged)" rather than being silently dropped.
    """
    checks: dict = {}
    runs_considered = 0
    for entry in entries:
        if not _included(entry.get("branch", ""), branch_prefix, branches):
            continue
        runs_considered += 1
        fired_this_run = set()
        for message in entry.get("hard_fails", []) + entry.get("warnings", []):
            match = _GATE_TAG_RE.match(message)
            check_name = match.group(1) if match else "(untagged)"
            bucket = checks.setdefault(check_name, {"fires": 0, "runs_fired_in": 0})
            bucket["fires"] += 1
            fired_this_run.add(check_name)
        for check_name in fired_this_run:
            checks[check_name]["runs_fired_in"] += 1
    return {"total_runs": runs_considered, "checks": checks}


def guard_firing_summary(branch_prefix: str | None = None, branches: set[str] | None = None) -> dict:
    """
    The single entry point both this file's own CLI and
    nova_coding_eval.generate_report() call. branch_prefix is the CLI-facing
    filter (e.g. "nova-agent/"); branches is exact-match, for a programmatic
    caller that already knows precisely which branch names belong to one
    eval run (nova_coding_eval.py passes this instead of a prefix, since
    every task's branch shares the same "nova-agent/" prefix as every other
    coding-agent task ever run -- a prefix alone couldn't isolate just this
    run's own tasks). Both default to None (no restriction, full historical
    log); giving both filters at once ANDs them together.
    """
    guard_entries = _parse_jsonl(GUARD_EVENTS_LOG_PATH)
    gate_entries = _parse_jsonl(GROUND_TRUTH_GATE_LOG_PATH)
    return {
        "guard_events": _tally_guard_events(guard_entries, branch_prefix, branches),
        "gate_checks": _tally_gate_checks(gate_entries, branch_prefix, branches),
    }


def format_guard_firing_summary(summary: dict) -> str:
    """
    Plain-text rendering shared by this file's own CLI and
    nova_coding_eval.py's generated report -- one table per source, sorted by
    fire count descending (the guards worth looking at first).
    """
    sections = [
        ("Runtime guards (nova_orchestrator_runpod.py)", "guard_events"),
        ("Completion-gate checks (nova_completion_gate.py)", "gate_checks"),
    ]
    lines = []
    for label, key in sections:
        section = summary[key]
        lines.append(f"{label} -- {section['total_runs']} task run(s) considered")
        if not section["checks"]:
            lines.append("  (none fired)")
            continue
        ranked = sorted(section["checks"].items(), key=lambda kv: kv[1]["fires"], reverse=True)
        for name, counts in ranked:
            lines.append(f"  {name}: fired {counts['fires']} time(s) across {counts['runs_fired_in']} run(s)")
        lines.append("")
    return "\n".join(lines).rstrip()


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Tally how often each RunPod coding-agent guard / completion-gate check has fired."
    )
    parser.add_argument(
        "--branch-prefix",
        default=None,
        help="Only count task runs whose branch starts with this prefix (e.g. one eval run's own branches).",
    )
    parser.add_argument("--json", action="store_true", help="Print the raw summary dict as JSON instead of a report.")
    args = parser.parse_args()

    result = guard_firing_summary(args.branch_prefix)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(format_guard_firing_summary(result))
