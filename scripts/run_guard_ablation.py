# scripts/run_guard_ablation.py
# 86bbcfv9d (Eval Harness Initiative 2, "audit existing gates individually").
#
# docs/aci-failure-mechanism-analysis.md measured nova_aci_harness.py's turn-loop guards
# CUMULATIVELY (baseline -> 2-guard -> 3-guard) -- it never isolated any one guard's own
# contribution. This script does the missing individual ablation: a baseline run with every
# guard on, then one run per ablatable guard with exactly that guard suppressed
# (--disable-guard NAME), full corpus, repeat=N per condition for Ollama sampling noise.
#
# Scope: the always-on turn-loop guards in nova_aci_harness.ABLATABLE_GUARDS --
# repeat_failed_call, done_without_edit, multiple_calls_ignored. (same_path_repeated_failure
# was in this set for the original audit; the audit found it net-negative and demoted it to
# opt-in --same-path-guard on 2026-08-30, so it's no longer an always-on guard to ablate.)
# --hybrid-verify / --early-abandon / --regression-guard are separate axes with their own
# flags and their own A/B scripts; this run leaves them at their defaults (hybrid-verify OFF,
# so this batch spends $0 -- Ollama only, no Anthropic calls).
#
# Original-audit outcome (2026-08-29, docs/aci-guard-cluster-ablation.md): uneven, as the
# runbook predicted. repeat_failed_call is the workhorse; multiple_calls_ignored is dormant
# for Qwen2.5-Coder-7B (0 fires in 300 runs); done_without_edit is a rare correctness net;
# same_path_repeated_failure was net-negative on turn-efficiency and got demoted. Pass rate
# stayed flat throughout -- these guards make failure legible/efficient, they don't lift the
# capability ceiling. The `would have fired` column tells you whether a flat result means
# "guard doesn't matter" or "guard just didn't get exercised much in this corpus".
#
# Runtime: (1 + N_guards) * 31 exercises * repeat. At repeat=2 that's ~310 runs, a few hours
# on this hardware -- run it backgrounded / overnight.
#
# Usage: nova-env/Scripts/python.exe scripts/run_guard_ablation.py [--repeat N] [--guards a,b]

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nova_aci_harness import ABLATABLE_GUARDS, OLLAMA_MODEL, run_all_exercises  # noqa: E402

DEFAULT_REPEATS = 2


def _stats(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["test_passed"])
    avg_turns = sum(r["turns_used"] for r in results) / total if total else 0.0
    max_turns_hit = sum(1 for r in results if r["final_status"] == "max_turns_reached")
    guard_totals: dict[str, int] = {}
    suppressed_totals: dict[str, int] = {}
    for r in results:
        for guard, count in r.get("guard_fires", {}).items():
            guard_totals[guard] = guard_totals.get(guard, 0) + count
        for guard, count in r.get("guards_suppressed", {}).items():
            suppressed_totals[guard] = suppressed_totals.get(guard, 0) + count
    status_totals: dict[str, int] = {}
    for r in results:
        status_totals[r["final_status"]] = status_totals.get(r["final_status"], 0) + 1
    return {
        "total": total,
        "passed": passed,
        "avg_turns": avg_turns,
        "max_turns_rate": max_turns_hit / total if total else 0.0,
        "guard_totals": guard_totals,
        "suppressed_totals": suppressed_totals,
        "status_totals": status_totals,
    }


def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="86bbcfv9d: individual guard ablation batch.")
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
    parser.add_argument(
        "--guards",
        default="",
        metavar="A,B",
        help=(
            "Comma-separated subset of guards to ablate individually "
            f"(default: all of {', '.join(sorted(ABLATABLE_GUARDS))})."
        ),
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="Skip the all-guards-on baseline condition (e.g. when re-running one guard).",
    )
    args = parser.parse_args()

    if args.guards:
        guards = [g.strip() for g in args.guards.split(",") if g.strip()]
        bad = set(guards) - ABLATABLE_GUARDS
        if bad:
            parser.error(f"unknown guard(s): {sorted(bad)}. Choices: {sorted(ABLATABLE_GUARDS)}")
    else:
        guards = sorted(ABLATABLE_GUARDS)

    conditions: list[tuple[str, frozenset[str]]] = []
    if not args.no_baseline:
        conditions.append(("baseline (all guards on)", frozenset()))
    for g in guards:
        conditions.append((f"-{g}", frozenset({g})))

    print(f"=== Guard ablation (86bbcfv9d) -- model={args.model}, repeat={args.repeat} per condition ===")
    print(f"Conditions: {len(conditions)}. hybrid-verify OFF (this batch spends $0).\n")

    stats: dict[str, dict] = {}
    for label, disabled in conditions:
        print(f"\n>>> Condition: {label}  (disabled_guards={sorted(disabled)})")
        results = run_all_exercises(repeats=args.repeat, model=args.model, disabled_guards=disabled)
        stats[label] = _stats(results)

    print("\n\n=== Ablation summary (86bbcfv9d) ===")
    header = f"{'Condition':<28}{'Pass rate':<12}{'Avg turns':<12}{'max_turns %':<13}{'guard would-have-fired'}"
    print(header)
    for label, _ in conditions:
        s = stats[label]
        wf = ""
        if label != "baseline (all guards on)":
            g = label[1:]  # strip leading "-"
            wf = f"{g}={s['suppressed_totals'].get(g, 0)}"
        row = f"{label:<28}{s['passed']}/{s['total']:<10}{s['avg_turns']:<12.2f}{s['max_turns_rate'] * 100:<13.1f}{wf}"
        print(row)

    if not args.no_baseline:
        base = stats["baseline (all guards on)"]
        print("\nDelta vs. baseline (negative avg-turns / max_turns% = guard was helping efficiency):")
        for label, _ in conditions:
            if label == "baseline (all guards on)":
                continue
            s = stats[label]
            d_pass = s["passed"] - base["passed"]
            d_turns = s["avg_turns"] - base["avg_turns"]
            d_max = (s["max_turns_rate"] - base["max_turns_rate"]) * 100
            print(f"  {label:<28} pass {d_pass:+d}   avg_turns {d_turns:+.2f}   max_turns% {d_max:+.1f}")

    print(
        "\nNote: pass rate is expected to stay flat -- these guards make failure legible/efficient, "
        "not more capable. Read avg_turns and max_turns% for a real effect, and cross-check "
        "'would-have-fired' -- a flat result on a guard that never fired is uninformative, not "
        "evidence the guard is useless."
    )


if __name__ == "__main__":
    main()
