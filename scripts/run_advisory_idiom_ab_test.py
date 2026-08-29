# scripts/run_advisory_idiom_ab_test.py
# Real A/B batch for the --advisory-idiom flag (nova_aci_harness.py, 86bbcfv9d),
# following the same real-batch-A/B methodology as
# scripts/run_regression_guard_ab_test.py: full corpus, baseline vs. the variant flag,
# repeat=N per condition for real sampling noise (Ollama isn't deterministic).
#
# Both conditions run with hybrid_verify=True -- --advisory-idiom has no effect without
# it. Both conditions also run with the default guards on (regression_guard=True), so this
# isolates ONE change: whether an IDIOM style verdict (passes every real test, just
# unidiomatic) blocks `done` (baseline) or is logged-and-accepted (--advisory-idiom).
#
# The question this answers: does removing the idiom nudge on an already-passing solution
# raise real pass rate? The hypothesis (docs/aci-hybrid-verify-gate-audit.md) is that the
# idiom nudge is the confirmed trigger for the octal loss-of-working-solution failure --
# the model breaks its own working code trying to satisfy a subjective style opinion.
# --regression-guard only catches that after the fact (and only ~2.4% of runs); accepting
# the passing solution up front should avoid the churn entirely.
#
# Because both arms already run the 3-way GAMED/IDIOM categorization, this script also
# reports, per arm: total style calls, runs with an IDIOM note, GAMED rejections. If IDIOM
# almost never fires in this corpus, a flat pass rate is uninformative (not evidence the
# split is wrong) -- same caveat as the regression-guard batch.
#
# DO NOT RUN without an explicit go-ahead -- real Anthropic Console spend (~$1-2 for a
# full-corpus repeat=2 batch: claude-sonnet-5 style calls, max_tokens=200, one per passing
# `done` attempt).
#
# Usage: nova-env/Scripts/python.exe scripts/run_advisory_idiom_ab_test.py [--repeat N]

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nova_aci_harness import OLLAMA_MODEL, run_all_exercises  # noqa: E402

DEFAULT_REPEATS = 2


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
    style_calls = sum(r.get("style_verifier_calls", 0) for r in results)
    idiom_note_runs = sum(1 for r in results if r.get("style_idiom_note"))
    gamed_rejections = sum(r.get("style_gamed_rejections", 0) for r in results)
    return {
        "total": total,
        "passed": passed,
        "avg_turns": avg_turns,
        "guard_totals": guard_totals,
        "status_totals": status_totals,
        "style_calls": style_calls,
        "idiom_note_runs": idiom_note_runs,
        "gamed_rejections": gamed_rejections,
    }


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="86bbcfv9d: advisory-idiom A/B batch.")
    parser.add_argument(
        "--model", default=OLLAMA_MODEL, metavar="TAG", help=f"Ollama model tag (default: {OLLAMA_MODEL})."
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=DEFAULT_REPEATS,
        metavar="N",
        help=f"Repeats per condition (default: {DEFAULT_REPEATS}).",
    )
    args = parser.parse_args()

    print(f"=== Advisory-idiom A/B test (86bbcfv9d) -- model={args.model}, repeat={args.repeat} per condition ===")
    print("Both conditions run with --hybrid-verify and --regression-guard on.\n")

    print(">>> Condition: baseline (advisory_idiom=False -- IDIOM blocks done, pre-split behavior)")
    baseline_results = run_all_exercises(
        repeats=args.repeat, hybrid_verify=True, regression_guard=True, advisory_idiom=False, model=args.model
    )

    print("\n>>> Condition: advisory-idiom (advisory_idiom=True -- IDIOM logged and accepted)")
    variant_results = run_all_exercises(
        repeats=args.repeat, hybrid_verify=True, regression_guard=True, advisory_idiom=True, model=args.model
    )

    baseline_stats = _stats(baseline_results)
    variant_stats = _stats(variant_results)

    print("\n\n=== A/B summary: advisory-idiom vs. baseline (86bbcfv9d) ===")
    header = f"{'Condition':<16}{'Pass rate':<12}{'Avg turns':<12}"
    header += f"{'Style calls':<13}{'IDIOM runs':<12}{'GAMED rej.'}"
    print(header)
    for name, s in (("baseline", baseline_stats), ("advisory-idiom", variant_stats)):
        row = f"{name:<16}{s['passed']}/{s['total']:<10}{s['avg_turns']:<12.2f}"
        row += f"{s['style_calls']:<13}{s['idiom_note_runs']:<12}{s['gamed_rejections']}"
        print(row)

    print("\nGuard fire breakdown:")
    all_guards = sorted(set(baseline_stats["guard_totals"]) | set(variant_stats["guard_totals"]))
    for guard in all_guards:
        b = baseline_stats["guard_totals"].get(guard, 0)
        v = variant_stats["guard_totals"].get(guard, 0)
        print(f"  {guard:<32} baseline={b:<6} advisory-idiom={v}")

    print("\nFinal status breakdown:")
    all_statuses = sorted(set(baseline_stats["status_totals"]) | set(variant_stats["status_totals"]))
    for status in all_statuses:
        b = baseline_stats["status_totals"].get(status, 0)
        v = variant_stats["status_totals"].get(status, 0)
        print(f"  {status:<32} baseline={b:<6} advisory-idiom={v}")

    print(
        "\nNote: the real question is whether advisory-idiom's pass rate beats baseline's without "
        "raising GAMED rejections (the split must not weaken the one high-value check). A flat pass "
        "rate with a near-zero IDIOM-note count in BOTH arms means this corpus's real "
        "'passing solution -> idiom reject -> regression' scenario simply didn't recur often enough "
        "to matter this batch, not that the split is wrong -- same caveat as the regression-guard batch."
    )


if __name__ == "__main__":
    main()
