# scripts/run_progress_framing_ab_test.py
# One-off A/B batch for the --progress-framing flag (nova_aci_harness.py), following the
# same real-batch-A/B methodology already used for the diff-format and context-window
# levers (see project memory "Nova ACI build"): full corpus, baseline vs. the variant flag,
# repeat=2 per condition for real-sampling noise (Ollama isn't deterministic).
#
# Filed against ClickUp 86bbjzguh -- the idle no-op-loop guard gap found while
# spot-checking 86bbjx8zp's pilot transcripts. This checks whether the progress-framing
# note (real edit/turn count appended to every tool result) changes behavior at all before
# deciding whether a hard guard is still needed on top of it.
#
# Usage: nova-env/Scripts/python.exe scripts/run_progress_framing_ab_test.py

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

    parser = argparse.ArgumentParser(description="86bbjzguh: progress-framing A/B batch.")
    parser.add_argument(
        "--model", default=OLLAMA_MODEL, metavar="TAG", help=f"Ollama model tag (default: {OLLAMA_MODEL})."
    )
    args = parser.parse_args()

    print(f"=== Progress-framing A/B test (86bbjzguh) -- model={args.model}, repeat={REPEATS} per condition ===\n")

    print(">>> Condition: baseline (progress_framing=False)")
    baseline_results = run_all_exercises(repeats=REPEATS, progress_framing=False, model=args.model)

    print("\n>>> Condition: progress-framing (progress_framing=True)")
    progress_results = run_all_exercises(repeats=REPEATS, progress_framing=True, model=args.model)

    baseline_stats = _stats(baseline_results)
    progress_stats = _stats(progress_results)

    print("\n\n=== A/B summary: progress-framing vs. baseline (86bbjzguh) ===")
    print(f"{'Condition':<18} {'Pass rate':<14} {'Avg turns':<12} {'Total guard fires'}")
    print(
        f"{'baseline':<18} "
        f"{baseline_stats['passed']}/{baseline_stats['total']:<12} "
        f"{baseline_stats['avg_turns']:<12.2f} "
        f"{sum(baseline_stats['guard_totals'].values())}"
    )
    print(
        f"{'progress-framing':<18} "
        f"{progress_stats['passed']}/{progress_stats['total']:<12} "
        f"{progress_stats['avg_turns']:<12.2f} "
        f"{sum(progress_stats['guard_totals'].values())}"
    )

    print("\nGuard fire breakdown:")
    all_guards = sorted(set(baseline_stats["guard_totals"]) | set(progress_stats["guard_totals"]))
    for guard in all_guards:
        b = baseline_stats["guard_totals"].get(guard, 0)
        p = progress_stats["guard_totals"].get(guard, 0)
        print(f"  {guard:<32} baseline={b:<6} progress-framing={p}")

    print("\nFinal status breakdown:")
    all_statuses = sorted(set(baseline_stats["status_totals"]) | set(progress_stats["status_totals"]))
    for status in all_statuses:
        b = baseline_stats["status_totals"].get(status, 0)
        p = progress_stats["status_totals"].get(status, 0)
        print(f"  {status:<32} baseline={b:<6} progress-framing={p}")

    print(
        "\nNote: max_turns_reached rate is the most directly relevant signal for whether "
        "progress-framing reduces idle/no-op-loop-driven turn exhaustion -- compare it "
        "above alongside pass rate before deciding whether a hard GUARD_IDLE_LOOP is still "
        "needed on top of this visibility-only intervention."
    )


if __name__ == "__main__":
    main()
