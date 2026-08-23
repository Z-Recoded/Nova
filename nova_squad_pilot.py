# nova_squad_pilot.py
# Proxy pilot for 86bbjx8zp (the shared-search-space hypothesis, filed while resolving
# 86bbh41p2 -- the specialist-squad architecture question). Tests whether giving a small
# local model a retrieved set of prior attempts on similar-shaped tasks -- what was tried,
# whether it worked -- measurably improves pass rate/turn-efficiency, without first
# building Code World Model's real Stage 3-5 branching search tree (86bbjrjtc, not built
# yet -- only Stages 1-2 are near-term scope there).
#
# Two phases, both built on nova_aci_harness.run_exercise() as the actual execution
# engine, so the only variable under test is whether extra retrieved context is present:
#   Phase 1 (--build-memory): run the model once on a set of "memory" exercises, capture
#     each run's task description + final solution attempt + real outcome as a flat
#     (task, attempt, outcome) record -- NOT a real branching search tree, a hand-built
#     stand-in for what Code World Model's Stage 5 would eventually generate at scale.
#   Phase 2 (--run): run held-out "eval" exercises twice, baseline (no extra context) vs.
#     retrieval (top-K memory records most similar to the eval task, by embedding cosine
#     similarity -- reusing nova_corrector.py's already-validated similarity pattern
#     rather than inventing a new one), and compare pass rate / turns / guard fires.
#
# Usage:
#   python nova_squad_pilot.py --build-memory           # Phase 1, ~24 real Ollama runs
#   python nova_squad_pilot.py --run --repeat 1          # Phase 2 smoke pass
#   python nova_squad_pilot.py --run --repeat 2           # Phase 2 real batch

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from chromadb.utils import embedding_functions

from nova_aci_harness import CORPUS_ROOT, _read_task_description, run_exercise

MEMORY_LOG_PATH = Path(__file__).parent / "logs" / "squad_pilot_memory.jsonl"
PILOT_LOG_PATH = Path(__file__).parent / "logs" / "squad_pilot_log.jsonl"

# Already vetted as a mechanically-reliable, mainline-lineage small model, per
# project memory qwen25-coder-fp16-small-model-findings (2026-08-22).
DEFAULT_MODEL = "qwen2.5-coder:3b-instruct-fp16"

# Hand-picked for spread, not random: raindrops/scrabble-score are familiar-shaped tasks
# with a real partial pass rate in the 122-run corpus study
# (docs/aci-task-familiarity-finding.md); bowling/zebra-puzzle are structurally unusual
# tasks that never passed once in that same study; luhn/poker were already run in the
# fp16 investigation, kept here for continuity across that prior work and this pilot.
DEFAULT_EVAL_SLUGS = ["raindrops", "scrabble-score", "bowling", "zebra-puzzle", "luhn", "poker"]

DEFAULT_K = 2
DEFAULT_REPEAT = 2

# Trimmed so one retrieved attempt doesn't dominate the prompt -- long enough to show real
# structure (most vendored solutions are well under this), short enough that k retrieved
# attempts stay a modest fraction of nova_aci_harness.OLLAMA_NUM_CTX.
SOLUTION_EXCERPT_CHARS = 1500


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Standard cosine similarity -- the same one-liner as nova_corrector._cosine_similarity,
    duplicated here rather than imported so this script doesn't pull in nova_corrector's
    own Second-Brain/Claude/training-flags dependencies, which have nothing to do with
    this pilot.
    """
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def _all_corpus_slugs() -> list[str]:
    """
    Every real vendored exercise slug under CORPUS_ROOT -- same listing
    nova_aci_harness.run_all_exercises() uses; NOTICE.md is skipped since it isn't a directory.
    """
    return sorted(p.name for p in CORPUS_ROOT.iterdir() if p.is_dir())


def build_attempt_memory(model: str, memory_slugs: list[str]) -> list[dict]:
    """
    Phase 1: runs `model` once on each of memory_slugs via the real ACI harness,
    capturing each run's task description, final solution-file content, and real
    outcome. A flat (task, attempt, outcome) record -- not a real branching search tree
    (Code World Model Stage 3-5, 86bbjrjtc, doesn't exist yet) -- built by hand for one
    model instead, matching 86bbjx8zp's own scope note. Overwrites MEMORY_LOG_PATH, so
    this is meant to be called with the full memory slug set each time, not incrementally.
    """
    embedding_fn = embedding_functions.DefaultEmbeddingFunction()
    records = []
    for i, slug in enumerate(memory_slugs, start=1):
        print(f"[{i}/{len(memory_slugs)}] Building memory: {slug} ({model})...")
        task_description = _read_task_description(CORPUS_ROOT / slug)
        result = run_exercise(slug, model=model)
        embedding = embedding_fn([task_description])[0]
        status = "PASS" if result["test_passed"] else "FAIL"
        print(f"  -> {status} ({result['final_status']}, {result['turns_used']} turn(s))")
        records.append(
            {
                "slug": slug,
                "model": model,
                "task_description": task_description,
                # Real bug found live: DefaultEmbeddingFunction() returns np.float32
                # elements, not native Python floats -- list(embedding) alone still isn't
                # JSON-serializable, so each element needs an explicit float() cast.
                "embedding": [float(x) for x in embedding],
                "solution_content": result["solution_content"],
                "test_passed": result["test_passed"],
                "final_status": result["final_status"],
                "turns_used": result["turns_used"],
            }
        )
    _write_memory(records)
    return records


def _write_memory(records: list[dict]) -> None:
    MEMORY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_LOG_PATH, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_attempt_memory() -> list[dict]:
    """Reloads a previously-built memory file, embeddings restored as np.ndarray for similarity math."""
    if not MEMORY_LOG_PATH.exists():
        raise RuntimeError(f"No attempt memory at {MEMORY_LOG_PATH} -- run --build-memory first.")
    records = []
    with open(MEMORY_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            record["embedding"] = np.array(record["embedding"])
            records.append(record)
    return records


def select_similar(
    task_description: str, memory: list[dict], embedding_fn, k: int = DEFAULT_K, exclude_slug: str | None = None
) -> list[dict]:
    """
    Ranks memory records by cosine similarity between their task_description embedding
    and the target task_description's own embedding -- the same DefaultEmbeddingFunction
    + cosine pattern nova_corrector.py already validated for near-duplicate query
    matching. exclude_slug is a defensive check against ever retrieving a record for the
    exact task being evaluated -- memory/eval slugs are disjoint by construction, but this
    guards against a future caller passing an overlapping split by mistake.
    """
    target_embedding = np.array(embedding_fn([task_description])[0])
    scored = [
        (record, _cosine_similarity(target_embedding, record["embedding"]))
        for record in memory
        if record["slug"] != exclude_slug
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [record for record, _ in scored[:k]]


def format_retrieved_context(records: list[dict]) -> str:
    """
    Renders retrieved memory records as a compact block -- both real successes and real
    failures included, since the model needs to see what didn't work too, not just what
    did (mirrors Code World Model Stage 5's own labeled-positive/labeled-negative framing,
    docs on 86bbjrjtc).
    """
    blocks = []
    for record in records:
        outcome = "PASSED all tests" if record["test_passed"] else f"did NOT pass ({record['final_status']})"
        excerpt = record["solution_content"][:SOLUTION_EXCERPT_CHARS]
        blocks.append(f"--- Prior attempt on '{record['slug']}' ({outcome}) ---\n```python\n{excerpt}\n```")
    return "\n\n".join(blocks)


def run_condition(
    eval_slugs: list[str],
    model: str,
    memory: list[dict],
    embedding_fn,
    use_retrieval: bool,
    repeats: int,
    k: int,
) -> list[dict]:
    """Runs every eval slug `repeats` times under one condition (baseline or retrieval), logging each result."""
    condition = "retrieval" if use_retrieval else "baseline"
    results = []
    total = len(eval_slugs) * repeats
    run_number = 0
    for slug in eval_slugs:
        task_description = _read_task_description(CORPUS_ROOT / slug)
        extra_context = ""
        if use_retrieval:
            retrieved = select_similar(task_description, memory, embedding_fn, k=k, exclude_slug=slug)
            extra_context = format_retrieved_context(retrieved)
        for rep in range(1, repeats + 1):
            run_number += 1
            print(f"[{condition} {run_number}/{total}] {slug} (rep {rep}/{repeats}, {model})...")
            result = run_exercise(slug, model=model, extra_context=extra_context)
            status = "PASS" if result["test_passed"] else "FAIL"
            print(f"  -> {status} ({result['final_status']}, {result['turns_used']} turn(s))")
            _log_pilot_result(result, condition=condition, k=k if use_retrieval else 0)
            results.append(result)
    return results


def _log_pilot_result(result: dict, condition: str, k: int) -> None:
    """
    Appends one run to PILOT_LOG_PATH, tagged with its condition/k -- kept separate from
    RESULTS_LOG_PATH (which run_exercise() already writes to) so this pilot's own
    baseline-vs-retrieval comparison never silently mixes into nova_aci_stats.py's
    assumptions about the main corpus log.
    """
    PILOT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {key: value for key, value in result.items() if key not in ("test_output", "solution_content")}
    entry["condition"] = condition
    entry["k"] = k
    with open(PILOT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _print_pilot_summary(baseline: list[dict], retrieval: list[dict]) -> None:
    """Real aggregate comparison between the two conditions -- pass rate, avg turns, total guard fires."""

    def _stats(results: list[dict]) -> tuple[int, int, float, int]:
        total = len(results)
        passed = sum(1 for r in results if r["test_passed"])
        avg_turns = sum(r["turns_used"] for r in results) / total if total else 0.0
        guard_total = sum(sum(r["guard_fires"].values()) for r in results)
        return total, passed, avg_turns, guard_total

    b_total, b_passed, b_turns, b_guards = _stats(baseline)
    r_total, r_passed, r_turns, r_guards = _stats(retrieval)

    print("\n=== Squad shared-search-space pilot summary (86bbjx8zp) ===")
    print(f"{'Condition':<12} {'Pass rate':<14} {'Avg turns':<12} {'Guard fires'}")
    print(f"{'baseline':<12} {f'{b_passed}/{b_total}':<14} {b_turns:<12.2f} {b_guards}")
    print(f"{'retrieval':<12} {f'{r_passed}/{r_total}':<14} {r_turns:<12.2f} {r_guards}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description=(
            "86bbjx8zp: proxy pilot testing whether retrieved prior-attempt context "
            "improves small local model coding competence, without building Code World "
            "Model's real Stage 3-5 search tree first."
        )
    )
    parser.add_argument(
        "--build-memory",
        action="store_true",
        help="Phase 1: build the flat attempt-memory (real Ollama runs, overwrites logs/squad_pilot_memory.jsonl).",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Phase 2: run baseline vs. retrieval conditions on the eval set and compare.",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, metavar="TAG", help=f"Ollama model tag (default: {DEFAULT_MODEL})."
    )
    parser.add_argument(
        "--memory-slugs",
        nargs="+",
        metavar="SLUG",
        help="Override the default memory-building slug set (default: every corpus slug not in --eval-slugs).",
    )
    parser.add_argument(
        "--eval-slugs",
        nargs="+",
        default=DEFAULT_EVAL_SLUGS,
        metavar="SLUG",
        help=f"Held-out slugs to evaluate (default: {DEFAULT_EVAL_SLUGS}).",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=DEFAULT_REPEAT,
        metavar="N",
        help=(
            "Repeats per (slug, condition) -- Ollama sampling isn't deterministic, same "
            "rationale as nova_aci_harness.py's own --repeat."
        ),
    )
    parser.add_argument(
        "--k",
        type=int,
        default=DEFAULT_K,
        metavar="K",
        help="Number of retrieved prior attempts injected per eval task.",
    )
    args = parser.parse_args()

    if not args.build_memory and not args.run:
        parser.error("Provide --build-memory and/or --run.")

    if args.build_memory:
        memory_slugs = args.memory_slugs or [s for s in _all_corpus_slugs() if s not in args.eval_slugs]
        build_attempt_memory(args.model, memory_slugs)
        print(f"\nWrote {len(memory_slugs)} memory records to {MEMORY_LOG_PATH}")

    if args.run:
        memory = load_attempt_memory()
        embedding_fn = embedding_functions.DefaultEmbeddingFunction()
        baseline_results = run_condition(
            args.eval_slugs, args.model, memory, embedding_fn, use_retrieval=False, repeats=args.repeat, k=args.k
        )
        retrieval_results = run_condition(
            args.eval_slugs, args.model, memory, embedding_fn, use_retrieval=True, repeats=args.repeat, k=args.k
        )
        _print_pilot_summary(baseline_results, retrieval_results)
