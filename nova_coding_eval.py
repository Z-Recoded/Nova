# nova_coding_eval.py
# Held-out comparison-report generator for the coding-agent brain swap
# (Phase 2 of the RunPod/Qwen2.5-Coder-32B coding-agent eval). Re-runs a
# fixed set of real historical merged tasks through the RunPod-backed
# coding agent (nova_orchestrator_runpod.py) and produces a side-by-side
# report against Claude's original result -- human-graded, no automated
# pass/fail. Matches Nova's standing discipline that a human always judges
# a worktree diff before it's trusted (nova_orchestrator.py never
# auto-merges).
#
# Run standalone:
#   nova-env\\Scripts\\python nova_coding_eval.py

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import nova_guard_stats
import nova_orchestrator
import nova_orchestrator_devstral
import nova_orchestrator_qwen3
import nova_orchestrator_runpod
from nova_completion_gate import check_ground_truth_completion, extract_task_requirements

LOGS_DIR = nova_orchestrator.LOGS_DIR
AGENT_LOG_PATH = nova_orchestrator.AGENT_LOG_PATH
TASK_OUTCOMES_LOG_PATH = nova_orchestrator.TASK_OUTCOMES_LOG_PATH
NOVA_REPO_ROOT = nova_orchestrator.NOVA_REPO_ROOT

# Non-representative smoke-test/infra-bug notes -- excluded from the held-out pool.
SMOKE_TEST_DENYLIST_SUBSTRINGS = [
    "Smoke test for",
    "pre-fix max_tokens",
    "Ran out of Anthropic API credit",
]

# Real merged tasks already directly tested against this exact RunPod model
# today (nova_runpod_toolcall_spike.py) -- excluded even though this one
# isn't caught by the smoke-test denylist above (its own outcome note is
# "Live end-to-end test of the new /code chat prefix...", not a smoke-test
# marker).
ALREADY_SPIKE_TESTED_BRANCHES = {
    "nova-agent/add-a-one-line-comment-above-the-sources-20260705-173537",
    # Merge commit a671fa6 (2026-07-28) is itself the review-split pipeline's
    # first live end-to-end confirmation run (86bb4gy0y) -- already tested
    # against this exact RunPod model, not a fresh held-out comparison point.
    "nova-agent/add-a-one-line-comment-above-supported-e-20260728-215210",
}

# Hand-verified branch -> merged commit map (2026-07-27) -- resolved once by
# checking `git log --all --grep="Written by nova_orchestrator.py"` against
# logs/agent_task_outcomes.jsonl, not a live heuristic. Task-text matching
# alone is provably ambiguous for this repo (3 separate commits share the
# SOURCES-comment subject line), so this map is the source of truth for
# these 6 tasks rather than a re-derived grep each run.
HELD_OUT_BRANCH_COMMITS = {
    "nova-agent/build-nova-s-self-monitoring-resource-he-20260705-151642": "c516cca",
    "nova-agent/make-start-nova-ps1-and-launch-openwebui-20260705-160541": "07365bd",
    "nova-agent/add-router-integration-so-a-coding-task--20260705-173104": "2ad8960",
    "nova-agent/add-the-nova-log-query-view-step-5-of-th-20260705-183610": "201c0a9",
    "nova-agent/build-a-real-rag-benchmark-suite-in-nova-20260705-184802": "9baf363",
    "nova-agent/add-the-nova-log-benchmark-view-step-3-o-20260705-214035": "1c278e4",
}

EXPECTED_HELD_OUT_COUNT = 6


def _load_outcomes() -> list[dict]:
    """Read every real entry from logs/agent_task_outcomes.jsonl."""
    with open(TASK_OUTCOMES_LOG_PATH, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def select_held_out_tasks() -> list[dict]:
    """
    Real merged tasks, excluding non-representative smoke-test/bug entries
    and anything already directly spike-tested against this model today.
    Asserts exactly EXPECTED_HELD_OUT_COUNT tasks -- a silent count drift
    here is exactly the kind of bug this harness exists to prevent.
    """
    selected = []
    for entry in _load_outcomes():
        if entry["outcome"] != "merged":
            continue
        if entry["branch"] in ALREADY_SPIKE_TESTED_BRANCHES:
            continue
        if any(marker in entry["note"] for marker in SMOKE_TEST_DENYLIST_SUBSTRINGS):
            continue
        selected.append(entry)

    if len(selected) != EXPECTED_HELD_OUT_COUNT:
        raise RuntimeError(
            f"Expected {EXPECTED_HELD_OUT_COUNT} held-out tasks, got {len(selected)}: "
            f"{[e['branch'] for e in selected]}. logs/agent_task_outcomes.jsonl may have "
            f"changed since this harness's exclusion lists were last verified -- update "
            f"ALREADY_SPIKE_TESTED_BRANCHES/HELD_OUT_BRANCH_COMMITS before proceeding."
        )
    return selected


def _find_merge_commit(branch: str) -> str:
    """Hand-verified map lookup -- see HELD_OUT_BRANCH_COMMITS's own comment above."""
    commit = HELD_OUT_BRANCH_COMMITS.get(branch)
    if not commit:
        raise RuntimeError(f"No verified commit mapping for branch {branch!r} -- add it to HELD_OUT_BRANCH_COMMITS.")
    return commit


def _git_show(commit: str) -> str:
    result = subprocess.run(
        ["git", "show", commit],
        cwd=NOVA_REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def _load_agent_log_entries_for_branch(branch: str) -> list[dict]:
    """Every agent_log.jsonl turn belonging to this branch, in turn order."""
    entries = []
    with open(AGENT_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("branch") == branch:
                entries.append(entry)
    entries.sort(key=lambda e: e.get("turn", 0))
    return entries


def _reconstruct_claude_result(entry: dict) -> dict:
    """
    Real diff (git show) + task description/turn count reconstructed from
    agent_log.jsonl for this branch. All 6 held-out tasks are "merged" --
    the discarded/budget_halt case (no surviving diff, transcript-only
    reconstruction) isn't needed for this specific set.
    """
    branch = entry["branch"]
    commit = _find_merge_commit(branch)
    log_entries = _load_agent_log_entries_for_branch(branch)
    task_description = log_entries[0]["task"] if log_entries else "(task text not found in agent_log.jsonl)"
    return {
        "branch": branch,
        "commit": commit,
        "task_description": task_description,
        "turns_used": len(log_entries),
        "diff": _git_show(commit),
        "note": entry.get("note", ""),
    }


def _create_worktree_at(slug: str, base_ref: str) -> tuple:
    """
    Like nova_orchestrator._create_worktree(), but branches from an
    arbitrary base_ref instead of current master. Real bug found live
    2026-07-27: nova_orchestrator._create_worktree() always branches from
    today's master, but every held-out task here was already merged into
    master back on 2026-07-05 -- branching from today's master meant each
    task's own target files (nova_headroom.py, its /headroom route, etc.)
    already existed correctly in the worktree before the model ever
    touched it, silently turning "build X" into "X already exists, do
    something to it" and invalidating the whole comparison. base_ref must
    be the commit immediately BEFORE this task's original work, i.e. the
    merged commit's own parent (<commit>^), not the merge commit itself.
    """
    branch_name = f"nova-agent/{slug}"
    worktree_path = Path(nova_orchestrator.NOVA_AGENT_WORKTREES_ROOT) / slug
    subprocess.run(
        ["git", "worktree", "add", str(worktree_path), "-b", branch_name, base_ref],
        cwd=NOVA_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return worktree_path, branch_name


def _git_diff_against_ref(root: str, base_ref: str) -> str:
    """
    Like nova_orchestrator._git_diff_against_master(), but against an
    arbitrary base_ref. Same `git add -N` fix (2026-08-01) so newly-created
    (untracked) files show up in the diff instead of being silently
    dropped -- see that function's docstring for why.
    """
    subprocess.run(["git", "add", "-N", "."], cwd=root, capture_output=True, text=True)
    result = subprocess.run(
        ["git", "diff", base_ref],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def run_runpod_backend(task_description: str, base_ref: str) -> dict:
    """
    Runs one task through the RunPod backend directly (bypassing
    run_coding_task()'s flag dispatch -- no need to flip runpod_coding_agent
    in nova_config.json for this script). Uses the condensed system prompt
    (build_condensed_system_prompt()), matching the real wired path in
    run_coding_task() -- the full CLAUDE.md version overflows this
    endpoint's 32768-token context window on anything beyond a couple of
    turns (real bug found and fixed 2026-07-27).

    base_ref: the pre-task commit this task's worktree must branch from --
    see _create_worktree_at()'s docstring for why this can't be today's
    master.
    """
    slug = nova_orchestrator._slugify(task_description)
    worktree_path, branch_name = _create_worktree_at(slug, base_ref)
    root = str(worktree_path)
    system_prompt = nova_orchestrator_runpod.build_condensed_system_prompt()
    messages = [{"role": "user", "content": task_description}]

    # Extracted once here (86bb72wd5) -- same "extract once, reuse for both
    # the allowlist guard and the completion gate" discipline as
    # nova_orchestrator.run_coding_task(). This script bypasses that
    # function entirely (see this function's own docstring), so it has to
    # do this extraction itself rather than inheriting it.
    requirements = extract_task_requirements(task_description)

    started_at = datetime.now()
    final_status, turns_used = nova_orchestrator_runpod.run_via_runpod(
        system_prompt,
        messages,
        root,
        slug,
        branch_name,
        task_description,
        None,
        None,
        False,
        nova_orchestrator.NOVA_AGENT_MAX_TURNS,
        nova_orchestrator_runpod.CODING_AGENT_MAX_OUTPUT_TOKENS,
        requirements,
    )
    elapsed_s = round((datetime.now() - started_at).total_seconds(), 1)
    diff = _git_diff_against_ref(root, base_ref)

    # Ground-truth completion gate (86bb71x39) -- real gap found live
    # 2026-08-01: this standalone eval script calls run_via_runpod()
    # directly, bypassing nova_orchestrator.run_coding_task() entirely, so
    # the gate that's supposed to catch exactly this eval's own false-
    # completion failures (Task 3's empty-diff-reported-as-"completed")
    # was never actually exercised by a single run of this harness. Uses
    # base_ref (this task's real pre-merge commit) instead of the
    # function's "master" default -- see check_ground_truth_completion()'s
    # own docstring, which names this exact eval-harness case.
    gate_result = check_ground_truth_completion(
        diff, task_description, root, base_ref=base_ref, requirements=requirements
    )
    nova_orchestrator._log_ground_truth_gate(branch_name, task_description, gate_result)

    return {
        "worktree_path": root,
        "branch": branch_name,
        "status": final_status,
        "turns_used": turns_used,
        "elapsed_s": elapsed_s,
        "diff": diff,
        "gate_result": gate_result,
    }


def run_devstral_backend(task_description: str, base_ref: str) -> dict:
    """
    Runs one task through the Devstral backend (nova_orchestrator_devstral,
    real native tool-calling) directly -- same bypass-the-flag-dispatch
    rationale and same return shape as run_runpod_backend(), so
    generate_report() can drive either backend through identical
    task-selection/gate-check/report-formatting logic (see its own
    `backend_fn` parameter).
    """
    slug = nova_orchestrator._slugify(task_description)
    worktree_path, branch_name = _create_worktree_at(slug, base_ref)
    root = str(worktree_path)
    system_prompt = nova_orchestrator_devstral.build_devstral_system_prompt()
    messages = [{"role": "user", "content": task_description}]

    requirements = extract_task_requirements(task_description)

    started_at = datetime.now()
    final_status, turns_used = nova_orchestrator_devstral.run_via_devstral(
        system_prompt,
        messages,
        root,
        slug,
        branch_name,
        task_description,
        None,
        None,
        False,
        nova_orchestrator.NOVA_AGENT_MAX_TURNS,
        nova_orchestrator_devstral.CODING_AGENT_MAX_OUTPUT_TOKENS,
        requirements,
    )
    elapsed_s = round((datetime.now() - started_at).total_seconds(), 1)
    diff = _git_diff_against_ref(root, base_ref)

    gate_result = check_ground_truth_completion(
        diff, task_description, root, base_ref=base_ref, requirements=requirements
    )
    nova_orchestrator._log_ground_truth_gate(branch_name, task_description, gate_result)

    return {
        "worktree_path": root,
        "branch": branch_name,
        "status": final_status,
        "turns_used": turns_used,
        "elapsed_s": elapsed_s,
        "diff": diff,
        "gate_result": gate_result,
    }


def run_qwen3_backend(task_description: str, base_ref: str) -> dict:
    """
    Runs one task through the Qwen3-Coder-Next backend
    (nova_orchestrator_qwen3, real native tool-calling via vLLM's
    qwen3_coder parser) directly -- same bypass-the-flag-dispatch rationale
    and same return shape as run_runpod_backend()/run_devstral_backend(),
    so generate_report() can drive any of the three backends through
    identical task-selection/gate-check/report-formatting logic (see its
    own `backend_fn` parameter).
    """
    slug = nova_orchestrator._slugify(task_description)
    worktree_path, branch_name = _create_worktree_at(slug, base_ref)
    root = str(worktree_path)
    system_prompt = nova_orchestrator_qwen3.build_qwen3_system_prompt()
    messages = [{"role": "user", "content": task_description}]

    requirements = extract_task_requirements(task_description)

    started_at = datetime.now()
    final_status, turns_used = nova_orchestrator_qwen3.run_via_qwen3(
        system_prompt,
        messages,
        root,
        slug,
        branch_name,
        task_description,
        None,
        None,
        False,
        nova_orchestrator.NOVA_AGENT_MAX_TURNS,
        nova_orchestrator_qwen3.CODING_AGENT_MAX_OUTPUT_TOKENS,
        requirements,
    )
    elapsed_s = round((datetime.now() - started_at).total_seconds(), 1)
    diff = _git_diff_against_ref(root, base_ref)

    gate_result = check_ground_truth_completion(
        diff, task_description, root, base_ref=base_ref, requirements=requirements
    )
    nova_orchestrator._log_ground_truth_gate(branch_name, task_description, gate_result)

    return {
        "worktree_path": root,
        "branch": branch_name,
        "status": final_status,
        "turns_used": turns_used,
        "elapsed_s": elapsed_s,
        "diff": diff,
        "gate_result": gate_result,
    }


def _format_gate_result(gate_result: dict) -> str:
    """
    Plain-text rendering of a check_ground_truth_completion() result for the
    report -- surfaced up front so a false "completed"/"max_turns_reached"
    status string (Task 3's real 2026-08-01 failure: an empty diff reported
    as "completed") can't hide from whoever reads this report next.
    """
    if gate_result["passed"]:
        lines = ["PASSED"]
    else:
        lines = ["FAILED"] + [f"  - {reason}" for reason in gate_result["hard_fails"]]
    if gate_result["warnings"]:
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in gate_result["warnings"])
    return "\n".join(lines)


def _report_section(index: int, claude_side: dict, backend_side: dict, backend_label: str) -> str:
    gate_text = _format_gate_result(backend_side["gate_result"])
    return (
        f"## Task {index}: {claude_side['task_description'][:100]}\n\n"
        f"**Full task description:**\n```\n{claude_side['task_description']}\n```\n\n"
        f"### Claude (original, merged as `{claude_side['commit']}`, {claude_side['turns_used']} turns)\n\n"
        f"```diff\n{claude_side['diff']}\n```\n\n"
        f"### {backend_label} (status: `{backend_side['status']}`, "
        f"{backend_side['turns_used']} turns, {backend_side['elapsed_s']}s)\n\n"
        f"Worktree: `{backend_side['worktree_path']}`\n\n"
        f"```diff\n{backend_side['diff']}\n```\n\n"
        f"**Ground-truth completion gate:**\n```\n{gate_text}\n```\n\n"
        f"**Verdict:** [ ] PASS   [ ] FAIL\n\n"
        f"**Notes:**\n\n\n"
        f"---\n\n"
    )


def generate_report(
    backend_fn=run_runpod_backend,
    backend_label: str = "RunPod / Qwen2.5-Coder-32B",
    report_tag: str = "",
) -> str:
    """
    Runs the full held-out set through `backend_fn` (same (task_description,
    base_ref) -> result-dict shape as run_runpod_backend()/
    run_devstral_backend()) and writes one timestamped Markdown report
    (never overwritten). No automated scoring -- Marvin fills in a
    pass/fail verdict per task by hand.

    backend_fn/backend_label/report_tag default to the original RunPod/Qwen
    comparison for backward compatibility -- pass
    backend_fn=run_devstral_backend, backend_label="RunPod / Devstral-
    Small-2507", report_tag="_devstral" to run the identical held-out
    comparison against the Devstral backend instead. All task-selection,
    gate-check, and guard/gate-summary logic below is shared unchanged
    between backends -- only which function actually executes each task
    differs.
    """
    tasks = select_held_out_tasks()
    sections = []
    run_branches = set()
    for i, entry in enumerate(tasks, start=1):
        claude_side = _reconstruct_claude_result(entry)
        base_ref = f"{claude_side['commit']}^"
        print(
            f"[{i}/{len(tasks)}] Running via {backend_label} (base {base_ref}): "
            f"{claude_side['task_description'][:70]}..."
        )
        backend_side = backend_fn(claude_side["task_description"], base_ref)
        print(
            f"  -> status={backend_side['status']} turns={backend_side['turns_used']} "
            f"elapsed={backend_side['elapsed_s']}s"
        )
        run_branches.add(backend_side["branch"])

        # Seeds real coding_review_log.jsonl data on every eval run, through
        # the exact same review path production dispatch will eventually use
        # (nova_orchestrator._review_coding_diff/_log_coding_review) -- not
        # gated on coding_review_pass, since that flag controls the
        # production dispatch-time call inside run_coding_task(), and this
        # standalone research script's whole point here is to produce review
        # data regardless of whether that flag is on.
        review = nova_orchestrator._review_coding_diff(backend_side["diff"], claude_side["task_description"])
        nova_orchestrator._log_coding_review(
            backend_side["branch"], claude_side["task_description"], backend_side["diff"], review
        )

        sections.append(_report_section(i, claude_side, backend_side, backend_label))

    # Guard-firing attribution (Marvin's ask, 2026-08-02) -- scoped to just
    # this run's own branches (run_branches), not the full historical log,
    # so this report answers "what fired THIS run" without older re-runs'
    # counts bleeding in. See nova_guard_stats.py's own header for why a
    # branch-prefix filter alone can't do this (every coding-agent task
    # shares the same "nova-agent/" prefix).
    guard_summary = nova_guard_stats.guard_firing_summary(branches=run_branches)
    guard_summary_text = nova_guard_stats.format_guard_firing_summary(guard_summary)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(LOGS_DIR, exist_ok=True)
    output_path = os.path.join(LOGS_DIR, f"coding_eval_report_{timestamp}{report_tag}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Coding-Agent Eval Report -- {backend_label} vs. Claude (original)\n\n")
        f.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        f.write("".join(sections))
        f.write("## Guard/Gate Firing Summary\n\n")
        f.write(
            "Which guards and completion-gate checks actually fired across this run's tasks -- "
            "see nova_guard_stats.py to re-run this against the full historical log.\n\n"
        )
        f.write(f"```\n{guard_summary_text}\n```\n")

    return output_path


if __name__ == "__main__":
    path = generate_report()
    print(f"\nReport written to: {path}")
