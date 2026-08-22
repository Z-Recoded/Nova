# nova_aci_stats.py
# Real statistics over logs/aci_harness_log.jsonl -- built 2026-08-15 after
# a single-pass run showed the exact same exercise (bob) pass 26/26 once
# and fail completely the next run, with identical code (Ollama's default
# sampling is not deterministic). A single pass/fail per exercise is a
# noisy one-sample estimate, not a trustworthy result -- this tool exists
# to turn accumulated repeated runs (nova_aci_harness.py --all --repeat N)
# into real per-exercise pass rates and correlation analysis, reusable
# across every future run rather than re-derived ad hoc each time.
#
# Usage:
#   python nova_aci_stats.py

import json
from pathlib import Path

import numpy as np

import nova_pull_exercism_corpus as corpus

RESULTS_LOG_PATH = Path(__file__).parent / "logs" / "aci_harness_log.jsonl"

# Pairs that are definitionally/mechanically linked, not a real behavioral
# finding -- excluded from "interesting" correlation output rather than
# silently included as if they meant something. Real example found live
# 2026-08-15: turns_used vs completed correlated at -0.981, which looks
# huge but is baked into the definitions (completed literally means
# "didn't hit the turn cap," and hitting the cap means turns_used == MAX_TURNS
# by construction) -- not a discovery about model behavior.
TAUTOLOGICAL_PAIRS = {frozenset({"turns_used", "completed"})}

NUMERIC_COLUMNS = ["difficulty", "turns_used", "completed", "test_passed", "parse_failures", "lenient_fraction"]


def _slug_to_difficulty() -> dict[str, int]:
    """Real difficulty rating per exercise slug, from the corpus module's own selection."""
    mapping = {}
    for difficulty, slugs in corpus.SELECTED_EXERCISES.items():
        for slug in slugs:
            mapping[slug] = difficulty
    return mapping


def load_results() -> list[dict]:
    """Every real logged run, oldest first. Empty list if the log doesn't exist yet."""
    if not RESULTS_LOG_PATH.exists():
        return []
    results = []
    with open(RESULTS_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                results.append(json.loads(line))
    return results


def build_numeric_table(results: list[dict]) -> dict[str, list[float]]:
    """
    One row per real logged run, as plain numeric columns -- the shape
    both the pass-rate report and the correlation matrix are built from.
    lenient_fraction is the share of a run's successful tool calls that
    needed the ast.literal_eval or repair tiers, not strict JSON.
    """
    slug_to_difficulty = _slug_to_difficulty()
    columns: dict[str, list[float]] = {c: [] for c in NUMERIC_COLUMNS}
    for r in results:
        pm = r["parse_method_counts"]
        total_calls = sum(pm.values())
        columns["difficulty"].append(slug_to_difficulty.get(r["slug"], 0))
        columns["turns_used"].append(r["turns_used"])
        # "completed" means "the model itself ended the run" (via done, accepted or
        # abandoned-after-nudge) as opposed to hitting the turn cap -- broadened from a
        # straight "== completed" check when GUARD_DONE_WITHOUT_EDIT (nova_aci_harness.py,
        # 2026-08-17) added "abandoned_after_nudge" as a second self-terminated status.
        columns["completed"].append(0 if r["final_status"] == "max_turns_reached" else 1)
        columns["test_passed"].append(1 if r["test_passed"] else 0)
        columns["parse_failures"].append(r["parse_failures"])
        # Any non-strict-json tier counts as lenient -- computed as a remainder
        # rather than naming each tier so a new repair heuristic (nova_aci_harness.py's
        # graduated _try_parse_raw chain) is automatically included here too.
        columns["lenient_fraction"].append((total_calls - pm["json"]) / total_calls if total_calls else 0.0)
    return columns


def pass_rates_by_slug(results: list[dict]) -> list[tuple[str, int, int]]:
    """
    (slug, passes, total_runs) for every exercise with at least one real
    logged run, sorted by pass rate descending.
    """
    by_slug: dict[str, list[dict]] = {}
    for r in results:
        by_slug.setdefault(r["slug"], []).append(r)
    rates = [(slug, sum(1 for run in runs if run["test_passed"]), len(runs)) for slug, runs in by_slug.items()]
    return sorted(rates, key=lambda row: (-(row[1] / row[2]), row[0]))


def correlation_matrix(columns: dict[str, list[float]]) -> dict[tuple[str, str], float]:
    """
    Pairwise Pearson correlation for every column pair. NaN for a pair
    where either column has zero variance (undefined, not zero).
    """
    arrays = {name: np.array(values, dtype=float) for name, values in columns.items()}
    matrix = {}
    for c1 in NUMERIC_COLUMNS:
        for c2 in NUMERIC_COLUMNS:
            if np.std(arrays[c1]) == 0 or np.std(arrays[c2]) == 0:
                matrix[(c1, c2)] = float("nan")
            else:
                matrix[(c1, c2)] = float(np.corrcoef(arrays[c1], arrays[c2])[0, 1])
    return matrix


def print_report() -> None:
    results = load_results()
    if not results:
        print(f"No results logged yet at {RESULTS_LOG_PATH}. Run nova_aci_harness.py first.")
        return

    n = len(results)
    unique_exercises = len({r["slug"] for r in results})
    print(f"=== {n} real logged run(s) across {unique_exercises} exercise(s) ===\n")

    print("Pass rate by exercise:")
    for slug, passed, total in pass_rates_by_slug(results):
        print(f"  {slug:<24} {passed}/{total}")

    status_totals: dict[str, int] = {}
    guard_totals: dict[str, int] = {}
    for r in results:
        status_totals[r["final_status"]] = status_totals.get(r["final_status"], 0) + 1
        for guard, count in r.get("guard_fires", {}).items():
            guard_totals[guard] = guard_totals.get(guard, 0) + count

    print("\nFinal status breakdown (all logged runs, old runs predate the guards below so carry no guard_fires):")
    for status in sorted(status_totals, key=lambda s: -status_totals[s]):
        print(f"  {status:<24} {status_totals[status]}")

    if guard_totals:
        print("\nGuard fire totals (docs/aci-failure-mechanism-analysis.md):")
        for guard, count in sorted(guard_totals.items(), key=lambda kv: -kv[1]):
            print(f"  {guard:<24} {count}")

    if n < 10:
        print(
            f"\nOnly {n} run(s) logged -- correlation analysis below is real but built on a "
            "small sample; treat it as a first look, not a confident answer. Run with "
            "--repeat to accumulate more before trusting any single number here."
        )

    columns = build_numeric_table(results)
    matrix = correlation_matrix(columns)

    print(f"\n{'':18}" + "".join(f"{c:>16}" for c in NUMERIC_COLUMNS))
    for c1 in NUMERIC_COLUMNS:
        row = f"{c1:18}"
        for c2 in NUMERIC_COLUMNS:
            row += f"{matrix[(c1, c2)]:16.3f}"
        print(row)

    print("\nTautological pairs (definitionally linked, not a real finding):")
    for c1 in NUMERIC_COLUMNS:
        for c2 in NUMERIC_COLUMNS:
            if c1 < c2 and frozenset({c1, c2}) in TAUTOLOGICAL_PAIRS:
                print(f"  {c1} <-> {c2}: r={matrix[(c1, c2)]:.3f}")

    print("\nStrongest real (non-tautological) correlation with test_passed:")
    candidates = [
        (c, matrix[("test_passed", c)])
        for c in NUMERIC_COLUMNS
        if c != "test_passed" and frozenset({"test_passed", c}) not in TAUTOLOGICAL_PAIRS
    ]
    candidates = [(c, r) for c, r in candidates if not np.isnan(r)]
    if candidates:
        best_col, best_r = max(candidates, key=lambda pair: abs(pair[1]))
        print(f"  test_passed <-> {best_col}: r={best_r:.3f}")
    else:
        print("  (no real-valued correlation available yet)")


if __name__ == "__main__":
    print_report()
