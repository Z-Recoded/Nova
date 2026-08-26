# scripts/run_early_abandon_ab_test.py
# Real A/B batch for the --early-abandon flag (nova_aci_harness.py, 86bbkru66), following
# the same real-batch-A/B methodology as scripts/run_progress_framing_ab_test.py: full
# corpus, baseline vs. the variant flag, repeat=2 per condition for real-sampling noise
# (Ollama isn't deterministic).
#
# Both conditions run with hybrid_verify=True -- early-abandon has no effect without it,
# since there is no pass-fraction signal to track otherwise. The question this answers:
# does stopping a stalled run early (SVT-inspired stall detection) preserve the real pass
# rate while cutting wasted turns on runs that were never going to converge, per the real
# 25/26 -> 0/26 collapse pattern found live in the max-hybrid-nudges probe?
#
# Usage: nova-env/Scripts/python.exe scripts/run_early_abandon_ab_test.py

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nova_aci_harness import OLLAMA_MODEL, run_all_exercises  # noqa: E402

REPEATS = 2


def _stats(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["test_passed"])
    avg_turns = sum(r["turns_used"] for r in results) / total if total else 0.0
    guard_totals: dict[str, int] = {}
    for r in results:
        for guard, count in r.get("guard_fires", {}).items():
            guard_totals[guard] = guard_totals.get(guard, 0) + count
    status_totals: dict[str, int] = {}
    for r in results:
        status_totals[r["final_status"]] = status_totals.get(r["final_status"], 0) + 1
    return {
        "total": total,
        "passed": passed,
        "avg_turns": avg_turns,
        "guard_totals": guard_totals,
        "status_totals": status_totals,
    }


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="86bbkru66: early-abandon A/B batch.")
    parser.add_argument(
        "--model", default=OLLAMA_MODEL, metavar="TAG", help=f"Ollama model tag (default: {OLLAMA_MODEL})."
    )
    args = parser.parse_args()

    print(f"=== Early-abandon A/B test (86bbkru66) -- model={args.model}, repeat={REPEATS} per condition ===")
    print("Both conditions run with --hybrid-verify on.\n")

    print(">>> Condition: baseline (hybrid_verify=True, early_abandon=False)")
    baseline_results = run_all_exercises(repeats=REPEATS, hybrid_verify=True, early_abandon=False, model=args.model)

    print("\n>>> Condition: early-abandon (hybrid_verify=True, early_abandon=True)")
    early_abandon_results = run_all_exercises(repeats=REPEATS, hybrid_verify=True, early_abandon=True, model=args.model)

    baseline_stats = _stats(baseline_results)
    early_abandon_stats = _stats(early_abandon_results)

    print("\n\n=== A/B summary: early-abandon vs. baseline (86bbkru66) ===")
    print(f"{'Condition':<18} {'Pass rate':<14} {'Avg turns':<12} {'Total guard fires'}")
    print(
        f"{'baseline':<18} "
        f"{baseline_stats['passed']}/{baseline_stats['total']:<12} "
        f"{baseline_stats['avg_turns']:<12.2f} "
        f"{sum(baseline_stats['guard_totals'].values())}"
    )
    print(
        f"{'early-abandon':<18} "
        f"{early_abandon_stats['passed']}/{early_abandon_stats['total']:<12} "
        f"{early_abandon_stats['avg_turns']:<12.2f} "
        f"{sum(early_abandon_stats['guard_totals'].values())}"
    )

    print("\nGuard fire breakdown:")
    all_guards = sorted(set(baseline_stats["guard_totals"]) | set(early_abandon_stats["guard_totals"]))
    for guard in all_guards:
        b = baseline_stats["guard_totals"].get(guard, 0)
        e = early_abandon_stats["guard_totals"].get(guard, 0)
        print(f"  {guard:<32} baseline={b:<6} early-abandon={e}")

    print("\nFinal status breakdown:")
    all_statuses = sorted(set(baseline_stats["status_totals"]) | set(early_abandon_stats["status_totals"]))
    for status in all_statuses:
        b = baseline_stats["status_totals"].get(status, 0)
        e = early_abandon_stats["status_totals"].get(status, 0)
        print(f"  {status:<32} baseline={b:<6} early-abandon={e}")

    print(
        "\nNote: the real question is whether early-abandon's avg-turns reduction (turns saved "
        "on runs that were never converging) comes with a pass-rate cost -- if pass rate holds "
        "flat while avg turns drops and 'abandoned_no_improvement' shows up only on runs that "
        "would have failed anyway, that's the real win. A pass-rate drop means early-abandon "
        "is cutting off runs that would have recovered given more turns."
    )


if __name__ == "__main__":
    main()
