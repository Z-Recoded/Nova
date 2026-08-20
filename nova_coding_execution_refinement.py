# nova_coding_execution_refinement.py
# Nova Training Pipeline Phase 2, coding module (86bbcfpbg): the "HERO"
# pattern -- small, high-quality, execution-grounded refinement, the
# opposite tradeoff from Phase 1's bulk, execution-free distillation
# (nova_bulk_distillation.py).
#
# Real gap this script resolves (scoped live, confirmed with Marvin): the
# ticket frames Phase 2 as refining Phase 1's own output, but Phase 1's 171
# rows are diffs against Nova's own git history, and Nova's codebase has no
# test suite -- there is nothing to execute to get a real pass/fail on them.
# Nova DOES already have real, proven test-execution infra --
# nova_aci_harness.py's _prepare_working_copy()/_run_real_tests() -- but it
# runs against the vendored Exercism corpus
# (data/coding_specialist_eval/exercism_subset/, 30 real exercises,
# MIT-licensed per docs/coding-specialist-exercise-corpus-plan.md), used
# today only to eval Qwen2.5-Coder-7B's own performance (the ACI harness),
# never to generate training data. This script redirects Phase 2's coding
# execution-grounding to that corpus instead. Phase 1's git-history rows
# stay as bulk semantic data, un-refined -- that was always their honest
# scope, not a gap this script needs to close.
#
# Loop, per exercise: Claude writes a full solution file -> real unittest
# execution (the SAME mechanism the ACI harness already uses to eval Qwen)
# -> on failure, Claude gets the real test failure output and tries again,
# up to MAX_REFINEMENT_ATTEMPTS times. The "diff" field (both the top-level
# one and each attempt's own) is always computed via difflib against the
# real original stub -- never model-generated -- so there is no diff-hunk
# application step to get wrong (unlike nova_aci_harness.py's own
# diff-format experiment, which was a real, tested regression for exactly
# that reason).
#
# Usage:
#   python nova_coding_execution_refinement.py --curate --dry-run    # zero-cost preview
#   python nova_coding_execution_refinement.py --curate --limit 3    # real paid pilot run
#   python nova_coding_execution_refinement.py --report
#   python nova_coding_execution_refinement.py --all

import argparse
import difflib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from nova_aci_harness import (
    CORPUS_ROOT,
    _prepare_working_copy,
    _read_task_description,
    _run_real_tests,
)
from nova_orchestrator import NOVA_AGENT_MODEL

# Same cp1252-crash precedent as every other Claude-API script in this repo
# -- a real generated solution can contain characters Windows' default
# console codepage can't encode.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, ".env"))


# ── Config ─────────────────────────────────────────────────────
OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "data", "coding_training", "execution_refinement")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "phase2_coding_trajectories.jsonl")

# 1 initial attempt + up to 2 real-test-grounded refinements. Loosely
# chosen, not tuned against real data yet -- same discipline
# nova_bulk_distillation.py's own MAX_CONTEXT_TOKENS comment documents.
MAX_REFINEMENT_ATTEMPTS = 3

# These are small, single-function exercises (unlike Phase 1's large
# from-scratch multi-file Nova-codebase tasks) -- a plain, non-streaming
# call is fine, no SDK-timeout risk to design around.
SOLUTION_MAX_TOKENS = 4096

# Keep the output file from bloating with huge unittest -v output.
TEST_OUTPUT_TRUNCATE_CHARS = 3000

# Sonnet 5's real confirmed list rates -- visibility only, never enforced.
# Duplicated locally rather than cross-imported, matching
# nova_bulk_distillation.py's own precedent (see its identical comment).
SONNET_5_INPUT_COST_PER_MTOK = 2.00
SONNET_5_OUTPUT_COST_PER_MTOK = 10.00

SYSTEM_PROMPT = (
    "You are completing a small, self-contained Python coding exercise. You will be given the "
    "task description and either a starter file to complete, or your own previous attempt plus "
    "the real output from running the exercise's test suite against it. Output ONLY the complete, "
    "corrected content of the solution file -- no explanation, no markdown code fences, no diff. "
    "Just the full, final Python source for the file."
)


# ── Solution generation ────────────────────────────────────────
def _strip_fences(raw: str) -> str:
    """Defensive fence-stripping even though the prompt asks for none --
    same real gotcha Phase 1's own request_bulk_solution() guards against."""
    return re.sub(r"^```(?:python)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE).strip()


def request_solution(
    client: anthropic.Anthropic,
    task: str,
    starter_content: str,
    prior_attempt: str | None = None,
    prior_test_output: str | None = None,
):
    """
    One Claude call producing a complete solution file. First attempt: task
    + starter stub. A refinement attempt additionally carries the previous
    full attempt and the REAL test failure output, so the fix is grounded
    in what actually happened, not a guess. Returns (solution_text, usage).
    """
    if prior_attempt is None:
        user_content = (
            f"Task:\n{task}\n\nStarter file to complete:\n\n```python\n{starter_content}\n```\n\n"
            "Write the complete solution file:"
        )
    else:
        user_content = (
            f"Task:\n{task}\n\nYour previous attempt:\n\n```python\n{prior_attempt}\n```\n\n"
            f"Running the real test suite against that attempt produced this output:\n\n"
            f"{prior_test_output}\n\nWrite a corrected, complete solution file:"
        )

    message = client.messages.create(
        model=NOVA_AGENT_MODEL,
        max_tokens=SOLUTION_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text_blocks = [block.text for block in message.content if block.type == "text"]
    if not text_blocks:
        return None, message.usage
    return _strip_fences(text_blocks[0]), message.usage


# ── Real execution loop ────────────────────────────────────────
def refine_and_verify(client: anthropic.Anthropic, slug: str) -> dict:
    """
    Runs one exercise through generate -> real-test-execute -> refine, up
    to MAX_REFINEMENT_ATTEMPTS times, stopping early on a real pass.
    Returns a dict with the task text, original stub, every attempt's
    content/pass-fail/test-output, and total real token usage.
    """
    module_name = slug.replace("-", "_")
    solution_filename = f"{module_name}.py"

    with tempfile.TemporaryDirectory() as tmp:
        working_copy = _prepare_working_copy(slug, Path(tmp))
        task = _read_task_description(working_copy)
        original_stub = (working_copy / solution_filename).read_text(encoding="utf-8")

        attempts = []
        total_input_tokens = 0
        total_output_tokens = 0
        prior_attempt = None
        prior_test_output = None

        for attempt_number in range(1, MAX_REFINEMENT_ATTEMPTS + 1):
            solution, usage = request_solution(client, task, original_stub, prior_attempt, prior_test_output)
            total_input_tokens += usage.input_tokens
            total_output_tokens += usage.output_tokens

            if solution is None:
                attempts.append(
                    {
                        "attempt": attempt_number,
                        "passed": False,
                        "diff": "",
                        "test_output": "(no solution text returned)",
                    }
                )
                break

            (working_copy / solution_filename).write_text(solution, encoding="utf-8")
            passed, test_output = _run_real_tests(working_copy, slug)
            attempt_diff = "".join(
                difflib.unified_diff(
                    original_stub.splitlines(keepends=True),
                    solution.splitlines(keepends=True),
                    fromfile=f"a/{solution_filename}",
                    tofile=f"b/{solution_filename}",
                )
            )
            attempts.append(
                {
                    "attempt": attempt_number,
                    "passed": passed,
                    "diff": attempt_diff,
                    "test_output": test_output[:TEST_OUTPUT_TRUNCATE_CHARS],
                }
            )

            if passed:
                break

            prior_attempt = solution
            prior_test_output = test_output[:TEST_OUTPUT_TRUNCATE_CHARS]

        return {
            "slug": slug,
            "task": task,
            "attempts": attempts,
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
        }


# ── JSONL read / write ─────────────────────────────────────────
def _load_processed_slugs() -> set[str]:
    if not os.path.exists(OUTPUT_PATH):
        return set()
    processed = set()
    with open(OUTPUT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                processed.add(json.loads(line)["slug"])
    return processed


def _append_row(row: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()


def _all_corpus_slugs() -> list[str]:
    """Same enumeration pattern as nova_aci_harness.run_all_exercises() --
    already excludes NOTICE.md (the corpus's own attribution file)."""
    return sorted(p.name for p in CORPUS_ROOT.iterdir() if p.is_dir())


# ── Main ───────────────────────────────────────────────────────
def curate(limit: int | None, dry_run: bool) -> None:
    all_slugs = _all_corpus_slugs()
    already_processed = _load_processed_slugs()
    remaining = [slug for slug in all_slugs if slug not in already_processed]

    print(f"{len(all_slugs)} exercise(s) in the corpus, {len(already_processed)} already processed.")
    print(f"{len(remaining)} remaining unprocessed exercise(s).")

    if limit is not None:
        remaining = remaining[:limit]
        print(f"Limited to {len(remaining)} exercise(s) this run.")

    if not remaining:
        print("Nothing to do.")
        return

    if dry_run:
        print("\n(dry run — no API calls, nothing written)")
        for slug in remaining:
            print(f"  would run: {slug}")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise OSError("ANTHROPIC_API_KEY environment variable is not set. Export it before running this script.")
    client = anthropic.Anthropic(api_key=api_key)

    total_input_tokens = 0
    total_output_tokens = 0
    written = 0
    verified_pass_count = 0

    for slug in remaining:
        print(f"\n{slug}")
        result = refine_and_verify(client, slug)
        total_input_tokens += result["total_input_tokens"]
        total_output_tokens += result["total_output_tokens"]

        attempts = result["attempts"]
        final_attempt = attempts[-1]
        final_passed = final_attempt["passed"]

        output_row = {
            "task": result["task"],
            "diff": final_attempt["diff"],
            "verification_status": "verified_pass" if final_passed else "verified_fail",
            "approved": final_passed,
            "slug": slug,
            "attempts": len(attempts),
            "source": "phase2_execution_refinement",
            "license": "mit",
        }
        if len(attempts) > 1:
            output_row["attempt_history"] = attempts

        _append_row(output_row)
        written += 1
        if final_passed:
            verified_pass_count += 1

        status = "PASS" if final_passed else "FAIL"
        print(f"  -> {status} after {len(attempts)} attempt(s)")

    cost = (
        total_input_tokens / 1_000_000 * SONNET_5_INPUT_COST_PER_MTOK
        + total_output_tokens / 1_000_000 * SONNET_5_OUTPUT_COST_PER_MTOK
    )
    print(f"\n{written} row(s) written to {OUTPUT_PATH} ({verified_pass_count}/{written} verified_pass)")
    print(
        f"Real usage this run: {total_input_tokens} input token(s), {total_output_tokens} output token(s), "
        f"~${cost:.4f} at Sonnet 5's introductory list rate (visibility only, not budget-enforced)."
    )


def report() -> None:
    """Print final counts, pass rate, and a couple of sample rows."""
    if not os.path.exists(OUTPUT_PATH):
        print(f"No curated file found at {OUTPUT_PATH} — run --curate first.")
        return

    with open(OUTPUT_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    total_corpus_size = len(_all_corpus_slugs())
    verified_pass = sum(1 for row in rows if row["verification_status"] == "verified_pass")
    avg_attempts = sum(row["attempts"] for row in rows) / len(rows) if rows else 0

    print(f"Total curated rows: {len(rows)} of {total_corpus_size} exercise(s) in the corpus.")
    print(f"Verified pass: {verified_pass}/{len(rows)}")
    print(f"Average attempts per exercise: {avg_attempts:.2f}\n")

    if rows:
        print("Sample row(s) (slug, status, attempts):")
        for row in rows[:3]:
            print(f"  {row['slug']}  |  {row['verification_status']}  |  {row['attempts']} attempt(s)")


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Nova Training Pipeline Phase 2 — coding, execution-grounded refinement (86bbcfpbg)"
    )
    parser.add_argument(
        "--curate", action="store_true", help="Generate + real-test-verify solutions, write curated rows"
    )
    parser.add_argument("--report", action="store_true", help="Print final counts + samples")
    parser.add_argument("--all", action="store_true", help="Run both phases in sequence")
    parser.add_argument("--limit", type=int, default=None, help="Cap how many new exercises to process this run")
    parser.add_argument("--dry-run", action="store_true", help="Preview which exercises would run, no API calls")
    args = parser.parse_args()

    if args.all or args.curate:
        curate(limit=args.limit, dry_run=args.dry_run)
    if args.all or args.report:
        report()
