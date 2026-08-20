# nova_coding_dpo_filter.py
# Nova Training Pipeline, coding track, Phase 4: DPO difficulty filtering (86bbcfpck).
#
# Real ambiguity found and resolved before building: 86bbcfpck's ticket text names
# nova_corrector.py, which is actually the CONVERSATION track's corrector (the Lore Pairs
# pipeline) -- not the coding track's. Confirmed with Marvin: target
# nova_coding_execution_refinement.py's Phase 2 output instead
# (data/coding_training/execution_refinement/phase2_coding_trajectories.jsonl) -- fresh, real,
# execution-grounded (rejected, chosen) pairs, not the older nova_coding_corrector.py corpus
# (already flagged in an earlier session as only 6 distinct underlying tasks repeated across
# runs -- a difficulty filter wouldn't fix that deeper diversity problem).
#
# A candidate pair is a Phase 2 row with attempts > 1 and a final verified_pass -- a genuine
# (last failing attempt, final passing attempt) transition. Phase 2's own design already filters
# out the "too hard, never resolved" end for free (a row that fails all attempts never produces
# a pair at all). This script's real job is the other end: among pairs that DID resolve, tell a
# sharp, informative near-miss (e.g. 20/21 real tests passing, one specific gap) apart from a
# pair carrying little specific signal (every real test failed, or a total crash before any test
# could even run) -- "not just whether a correction happened, but whether it's a useful training
# signal," per the ticket.
#
# Usage:
#   python nova_coding_dpo_filter.py --filter   # re-derive from Phase 2's current output
#   python nova_coding_dpo_filter.py --report

import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

PHASE2_INPUT_PATH = os.path.join(
    _SCRIPT_DIR, "data", "coding_training", "execution_refinement", "phase2_coding_trajectories.jsonl"
)
OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "data", "coding_training", "dpo_pairs")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "phase4_coding_dpo_pairs.jsonl")

# Provisional thresholds, chosen against today's real 8-pair pilot, not statistically tuned --
# same "loosely chosen, not tuned yet" discipline as Phase 1's MAX_CONTEXT_TOKENS/Phase 2's
# MAX_REFINEMENT_ATTEMPTS. Revisit once real DPO training on this data is attempted.
TOO_EASY_MAX_DIFF_LINES = 3
TOO_HARD_MAX_PASS_RATIO = 0.2


# ── Real signal extraction from Phase 2's own stored test_output ──
def _count_real_test_results(test_output: str) -> tuple[int, int]:
    """
    Real per-test pass/fail counts from unittest -v's own verbose marker
    ("... ok" / "... FAIL" / "... ERROR" at the end of each test's line) --
    confirmed live this is the reliable signal, not the "FAIL: <name>"
    summary-block header, which can fall outside Phase 2's 3000-char
    test_output truncation on a test suite with many cases. Both zero on a
    real module-level crash before any test could even be collected
    (confirmed live via zebra-puzzle's real traceback) -- a distinct,
    meaningful signal from "ran and failed."
    """
    tests_ok = test_output.count("... ok")
    tests_fail = test_output.count("... FAIL") + test_output.count("... ERROR")
    return tests_ok, tests_fail


def _classify(diff_lines: int, pass_ratio: float) -> str:
    if diff_lines < TOO_EASY_MAX_DIFF_LINES:
        return "too_easy"
    if pass_ratio < TOO_HARD_MAX_PASS_RATIO:
        return "too_hard_low_signal"
    return "keep"


# ── Pair extraction ────────────────────────────────────────────
def _extract_candidate_pairs(phase2_rows: list[dict]) -> list[dict]:
    """
    A candidate pair is a Phase 2 row with attempts > 1 and a final
    verified_pass. rejected = the last attempt in attempt_history where
    passed is false; chosen = the row's own final diff (its last, passing
    attempt). No new execution -- pure re-read of Phase 2's already-
    verified output.
    """
    pairs = []
    for row in phase2_rows:
        if row["attempts"] <= 1 or row["verification_status"] != "verified_pass":
            continue

        failing_attempts = [a for a in row["attempt_history"] if not a["passed"]]
        if not failing_attempts:
            continue
        rejected_attempt = failing_attempts[-1]

        tests_ok, tests_fail = _count_real_test_results(rejected_attempt["test_output"])
        pass_ratio = tests_ok / (tests_ok + tests_fail) if (tests_ok + tests_fail) > 0 else 0.0
        diff_lines = len(row["diff"].splitlines())
        classification = _classify(diff_lines, pass_ratio)

        pairs.append(
            {
                "task": row["task"],
                "rejected_diff": rejected_attempt["diff"],
                "chosen_diff": row["diff"],
                "slug": row["slug"],
                "tests_ok": tests_ok,
                "tests_fail": tests_fail,
                "pass_ratio": round(pass_ratio, 4),
                "diff_lines": diff_lines,
                "classification": classification,
                "kept": classification == "keep",
                "source": "phase4_coding_dpo_filter",
            }
        )
    return pairs


# ── Main ───────────────────────────────────────────────────────
def filter_pairs() -> None:
    if not os.path.exists(PHASE2_INPUT_PATH):
        print(f"No Phase 2 output found at {PHASE2_INPUT_PATH} — run nova_coding_execution_refinement.py first.")
        return

    with open(PHASE2_INPUT_PATH, encoding="utf-8") as f:
        phase2_rows = [json.loads(line) for line in f if line.strip()]

    pairs = _extract_candidate_pairs(phase2_rows)
    print(f"{len(phase2_rows)} row(s) in Phase 2's output, {len(pairs)} real candidate pair(s) found.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        for pair in pairs:
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    kept = sum(1 for p in pairs if p["kept"])
    print(f"{kept}/{len(pairs)} kept, written to {OUTPUT_PATH} (filtered-out pairs are written too, not dropped).")
    for pair in pairs:
        status = "KEEP" if pair["kept"] else f"FILTER ({pair['classification']})"
        total_tests = pair["tests_ok"] + pair["tests_fail"]
        print(
            f"  {pair['slug']:20s} pass_ratio={pair['pass_ratio']:.2f} "
            f"({pair['tests_ok']}/{total_tests}) diff_lines={pair['diff_lines']:3d} -> {status}"
        )


def report() -> None:
    if not os.path.exists(OUTPUT_PATH):
        print(f"No filtered output found at {OUTPUT_PATH} — run --filter first.")
        return

    with open(OUTPUT_PATH, encoding="utf-8") as f:
        pairs = [json.loads(line) for line in f if line.strip()]

    kept = sum(1 for p in pairs if p["kept"])
    by_classification: dict[str, int] = {}
    for pair in pairs:
        by_classification[pair["classification"]] = by_classification.get(pair["classification"], 0) + 1

    print(f"Total candidate pairs: {len(pairs)}")
    print(f"Kept: {kept}/{len(pairs)}")
    for classification, count in sorted(by_classification.items()):
        print(f"  {classification}: {count}")


# ── CLI ────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Nova Training Pipeline, coding track — Phase 4: DPO difficulty filtering (86bbcfpck)"
    )
    parser.add_argument("--filter", action="store_true", help="Re-derive filtered pairs from Phase 2's current output")
    parser.add_argument("--report", action="store_true", help="Print counts by classification")
    parser.add_argument("--all", action="store_true", help="Run both phases in sequence")
    args = parser.parse_args()

    if args.all or args.filter:
        filter_pairs()
    if args.all or args.report:
        report()
