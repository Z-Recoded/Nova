# nova_eval_held_out_report.py
# The held-out generalization pass for the coding sub-agent's ground-truth
# completion gate (ClickUp 86bbcfv9d, "Eval Harness -- Initiative 2", gate
# #1). Sibling of nova_coding_eval.py: that file re-runs the DEV set (tasks
# already used to tune the gate); this one runs the genuinely held-out pool
# from nova_eval_held_out.py, whose whole purpose is to test whether a gate
# check generalizes to failure shapes it was never built from.
#
# What it does, per held-out task:
#   1. Create a disposable worktree at the task's frozen base_ref.
#   2. Drive the real production Claude turn loop
#      (nova_orchestrator.run_via_claude) against it -- the exact path
#      production coding tasks use, not a reimplementation.
#   3. Diff the worktree, run check_ground_truth_completion() on it with the
#      task's FROZEN requirements (never re-extracted -- see HeldOutTask's
#      own docstring for why).
#   4. Write everything into one timestamped Markdown report for a human to
#      read and fill in per-check "generalizes / doesn't / hurts OOD
#      precision" verdicts.
#
# Deliberately does NOT touch any shared training/telemetry log:
#   - No nova_orchestrator._log_ground_truth_gate() -- held-out gate results
#     must not land in logs/ground_truth_gate_log.jsonl, which dev-set
#     audits read.
#   - No _review_coding_diff()/_log_coding_review() -- that seeds
#     logs/coding_review_log.jsonl, which nova_coding_corrector.py turns
#     into DPO training pairs. nova_coding_eval.generate_report() does this
#     unconditionally; this runner must not, or the held-out pool is
#     contaminated the first time it runs.
#
# Run standalone:
#   nova-env\\Scripts\\python nova_eval_held_out_report.py --dry-run   # $0, lists tasks
#   nova-env\\Scripts\\python nova_eval_held_out_report.py             # real Anthropic API $

import argparse
import os
import re
import subprocess
from datetime import datetime

import anthropic

import nova_eval_held_out
import nova_orchestrator
from nova_coding_eval import _create_worktree_at, _git_diff_against_ref
from nova_completion_gate import check_ground_truth_completion
from nova_eval_held_out import HeldOutTask

LOGS_DIR = nova_orchestrator.LOGS_DIR
NOVA_REPO_ROOT = nova_orchestrator.NOVA_REPO_ROOT


def _resolve_ref(ref: str) -> str:
    """Return the short SHA `ref` resolves to, or raise if it doesn't exist."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", ref],
        cwd=NOVA_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"base_ref {ref!r} does not resolve in this repo: {result.stderr.strip()}")
    return result.stdout.strip()


def run_claude_held_out(task: HeldOutTask) -> dict:
    """
    Run one held-out task through the production Claude turn loop and check
    its diff against the ground-truth completion gate. Same
    (worktree/status/turns/diff/gate) result shape as
    nova_coding_eval.run_runpod_backend() so the report formatting stays
    simple.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise OSError("ANTHROPIC_API_KEY is not set -- export it before running the held-out pass (metered API).")

    slug = nova_orchestrator._slugify(task.task_description)
    worktree_path, branch_name = _create_worktree_at(slug, task.base_ref)
    root = str(worktree_path)

    system_prompt = nova_orchestrator._build_system_prompt(root)
    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": task.task_description}]

    started_at = datetime.now()
    final_status, turns_used = nova_orchestrator.run_via_claude(
        client,
        system_prompt,
        messages,
        root,
        slug,
        branch_name,
        task.task_description,
        None,  # skill_category -- skill injection is off for this pass
        None,  # skill_version
        False,  # budget_gate_enabled -- the eval isn't subject to the production token budget
        nova_orchestrator.NOVA_AGENT_MAX_TURNS,
    )
    elapsed_s = round((datetime.now() - started_at).total_seconds(), 1)

    diff = _git_diff_against_ref(root, task.base_ref)

    # Frozen requirements, base_ref from the task -- NOT re-extracted. The
    # held-out pool's contract is that nothing about a task changes between
    # runs.
    gate_result = check_ground_truth_completion(
        diff, task.task_description, root, base_ref=task.base_ref, requirements=task.requirements
    )

    return {
        "task_id": task.task_id,
        "worktree_path": root,
        "branch": branch_name,
        "status": final_status,
        "turns_used": turns_used,
        "elapsed_s": elapsed_s,
        "diff": diff,
        "gate_result": gate_result,
    }


# ── Report formatting ────────────────────────────────────

# Every check check_ground_truth_completion() can tag, in the order it runs
# them -- so the per-task verdict table always lists all of them, including
# the ones that didn't fire (a check that stays quiet on a good diff is
# itself a data point for the generalization verdict).
ALL_GATE_CHECKS = [
    "nonzero_diff",
    "syntax_valid",
    "powershell_syntax_valid",
    "lint_clean",
    "module_level_name_order",
    "cross_module_circular_import",
    "cross_module_missing_export",
    "required_files_touched",
    "forbidden_paths_untouched",
    "narrow_scope_not_exceeded",
    "deliverables_present",
    "unused_new_import",
    "unexpected_deletion",
]


# nova_completion_gate._tag()'s format: "[check_name] rest of message".
# Same regex nova_guard_stats._GATE_TAG_RE uses.
_GATE_TAG_RE = re.compile(r"^\[(\w+)\] ")


def _checks_that_fired(gate_result: dict) -> set:
    """The set of check names that produced any hard_fail or warning this run."""
    fired = set()
    for message in gate_result["hard_fails"] + gate_result["warnings"]:
        match = _GATE_TAG_RE.match(message)
        if match:
            fired.add(match.group(1))
    return fired


def _format_task_section(index: int, task: HeldOutTask, result: dict) -> str:
    gate = result["gate_result"]
    fired = _checks_that_fired(gate)

    verdict_rows = "\n".join(
        f"| `{check}` | {'FIRED' if check in fired else 'quiet'} |  |" for check in ALL_GATE_CHECKS
    )
    hard_fail_lines = "\n".join(f"  - {h}" for h in gate["hard_fails"]) or "  (none)"
    warning_lines = "\n".join(f"  - {w}" for w in gate["warnings"]) or "  (none)"

    return (
        f"## Task {index}: `{task.task_id}`\n\n"
        f"**Description:**\n```\n{task.task_description}\n```\n\n"
        f"**Frozen requirements:** `{task.requirements}`\n\n"
        f"**Base ref:** `{task.base_ref}` ({_resolve_ref(task.base_ref)})\n\n"
        f"### Claude result (status: `{result['status']}`, {result['turns_used']} turns, {result['elapsed_s']}s)\n\n"
        f"Worktree: `{result['worktree_path']}`\n\n"
        f"```diff\n{result['diff']}\n```\n\n"
        f"### Ground-truth completion gate\n\n"
        f"**Passed:** `{gate['passed']}`\n\n"
        f"Hard fails:\n{hard_fail_lines}\n\n"
        f"Warnings:\n{warning_lines}\n\n"
        f"### Per-check generalization verdict (fill in)\n\n"
        f"| Check | This run | Verdict (generalizes / doesn't / hurts OOD precision) |\n"
        f"|---|---|---|\n"
        f"{verdict_rows}\n\n"
        f"---\n\n"
    )


def generate_report(dry_run: bool = False) -> str:
    """
    Run every held-out task through run_claude_held_out() and write one
    timestamped Markdown report to logs/. dry_run resolves each base_ref and
    prints the frozen requirements without spending an API call -- use it to
    sanity-check the pool before the real (metered) run.
    """
    tasks = nova_eval_held_out.select_held_out_tasks()

    if dry_run:
        print(f"Dry run -- {len(tasks)} held-out task(s), no API calls:\n")
        for i, task in enumerate(tasks, start=1):
            print(f"[{i}] {task.task_id}")
            print(f"    base_ref: {task.base_ref} -> {_resolve_ref(task.base_ref)}")
            print(f"    requirements: {task.requirements}")
            print(f"    source: {task.source}, added: {task.added}\n")
        return ""

    sections = []
    for i, task in enumerate(tasks, start=1):
        print(f"[{i}/{len(tasks)}] Running held-out task {task.task_id} via Claude (base {task.base_ref})...")
        result = run_claude_held_out(task)
        print(
            f"  -> status={result['status']} turns={result['turns_used']} "
            f"elapsed={result['elapsed_s']}s gate_passed={result['gate_result']['passed']}"
        )
        sections.append(_format_task_section(i, task, result))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(LOGS_DIR, exist_ok=True)
    output_path = os.path.join(LOGS_DIR, f"held_out_generalization_report_{timestamp}.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Held-Out Generalization Pass -- Ground-Truth Completion Gate\n\n")
        f.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        f.write(
            "Candidate model: Claude (nova_orchestrator.run_via_claude, the production coding path). "
            "One agentic run per task. No shared training/telemetry log was written -- see this "
            "module's header.\n\n"
        )
        f.write("".join(sections))

    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the held-out generalization pass on the completion gate.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List held-out tasks and resolve their base_refs without any API call ($0).",
    )
    args = parser.parse_args()

    path = generate_report(dry_run=args.dry_run)
    if path:
        print(f"\nReport written to: {path}")
