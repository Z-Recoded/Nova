# scripts/run_regression_guard_ab_test.py
# Real A/B batch for the --regression-guard flag (nova_aci_harness.py, 86bbmj2hw),
# following the same real-batch-A/B methodology as
# scripts/run_early_abandon_ab_test.py: full corpus, baseline vs. the variant flag,
# repeat=2 per condition for real-sampling noise (Ollama isn't deterministic).
#
# Both conditions run with hybrid_verify=True -- regression-guard has no effect without
# it, since there is no pass-fraction signal to track otherwise. The question this
# answers: does restoring a genuinely 100%-passing snapshot (lost to a later regression
# while chasing a style nudge -- the real octal failure mode found live 2026-08-25)
# actually recover real pass rate, and does the regression-aware nudge text change model
# behavior at all?
#
# Usage: nova-env/Scripts/python.exe scripts/run_regression_guard_ab_test.py

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
    restored_count = sum(1 for r in results if r.get("snapshot_restored"))
    return {
        "total": total,
        "passed": passed,
        "avg_turns": avg_turns,
        "guard_totals": guard_totals,
        "status_totals": status_totals,
        "restored_count": restored_count,
    }


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="86bbmj2hw: regression-guard A/B batch.")
    parser.add_argument(
        "--model", default=OLLAMA_MODEL, metavar="TAG", help=f"Ollama model tag (default: {OLLAMA_MODEL})."
    )
    args = parser.parse_args()

    print(f"=== Regression-guard A/B test (86bbmj2hw) -- model={args.model}, repeat={REPEATS} per condition ===")
    print("Both conditions run with --hybrid-verify on.\n")

    print(">>> Condition: baseline (hybrid_verify=True, regression_guard=False)")
    baseline_results = run_all_exercises(repeats=REPEATS, hybrid_verify=True, regression_guard=False, model=args.model)

    print("\n>>> Condition: regression-guard (hybrid_verify=True, regression_guard=True)")
    guard_results = run_all_exercises(repeats=REPEATS, hybrid_verify=True, regression_guard=True, model=args.model)

    baseline_stats = _stats(baseline_results)
    guard_stats = _stats(guard_results)

    print("\n\n=== A/B summary: regression-guard vs. baseline (86bbmj2hw) ===")
    print(f"{'Condition':<18} {'Pass rate':<14} {'Avg turns':<12} {'Total guard fires':<20} {'Snapshots restored'}")
    print(
        f"{'baseline':<18} "
        f"{baseline_stats['passed']}/{baseline_stats['total']:<12} "
        f"{baseline_stats['avg_turns']:<12.2f} "
        f"{sum(baseline_stats['guard_totals'].values()):<20} "
        f"{baseline_stats['restored_count']}"
    )
    print(
        f"{'regression-guard':<18} "
        f"{guard_stats['passed']}/{guard_stats['total']:<12} "
        f"{guard_stats['avg_turns']:<12.2f} "
        f"{sum(guard_stats['guard_totals'].values()):<20} "
        f"{guard_stats['restored_count']}"
    )

    print("\nGuard fire breakdown:")
    all_guards = sorted(set(baseline_stats["guard_totals"]) | set(guard_stats["guard_totals"]))
    for guard in all_guards:
        b = baseline_stats["guard_totals"].get(guard, 0)
        g = guard_stats["guard_totals"].get(guard, 0)
        print(f"  {guard:<32} baseline={b:<6} regression-guard={g}")

    print("\nFinal status breakdown:")
    all_statuses = sorted(set(baseline_stats["status_totals"]) | set(guard_stats["status_totals"]))
    for status in all_statuses:
        b = baseline_stats["status_totals"].get(status, 0)
        g = guard_stats["status_totals"].get(status, 0)
        print(f"  {status:<32} baseline={b:<6} regression-guard={g}")

    print(
        "\nNote: the real question is whether regression-guard's pass rate beats baseline's -- "
        "any run where 'snapshots restored' is nonzero on the regression-guard side but zero on "
        "baseline is direct evidence of a run that baseline lost to the regression-during-nudging "
        "failure mode and regression-guard recovered. A flat pass rate with zero restores across "
        "both conditions means this batch's real octal-style scenario (100% pass -> style reject "
        "-> regression) simply didn't recur often enough in this corpus to matter, not that the "
        "fix is broken."
    )


if __name__ == "__main__":
    main()
