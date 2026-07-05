# nova_benchmark.py
# Performance benchmarking for Nova.
#
# Two independent benchmarks live in this file:
#   1. Context-window benchmark (test_context_size / run_benchmark) — finds
#      the largest num_ctx Ollama can handle on this machine. Original
#      capability, unchanged.
#   2. Golden-query RAG benchmark (run_golden_benchmark) — runs a fixed set
#      of real queries through the full nova_query.ask() pipeline to
#      establish a base-model baseline (latency, routing accuracy, blend
#      rate), gating future base-model swaps per CLAUDE.md Phase 3's swap
#      trigger.

import argparse
import json
import os
import sys
import time
from datetime import datetime

import ollama

import nova_query
from nova_logger import detect_blending

# Windows' default console codepage (cp1252) can't encode the ✓/✗ characters
# this file prints — reconfiguring stdout to UTF-8 avoids a crash when this
# is run from a plain PowerShell/cmd prompt instead of a UTF-8-aware terminal.
sys.stdout.reconfigure(encoding="utf-8")

# ── Config: context-window benchmark ───────────────────────────
MODEL = "llama3.2"
TEST_SIZES = [2048, 4096, 8192, 16384, 32768]

# A prompt that's short but forces the model to reason
TEST_PROMPT = "Summarize what a knowledge base assistant does in two sentences."

# ── Config: golden-query RAG benchmark ─────────────────────────
LOGS_DIR = "C:/Nova/logs"
BENCHMARK_LOG_PATH = "C:/Nova/logs/benchmark_log.jsonl"

# One query per nova_router.py category. Used to establish and re-check the
# Llama 3.2 3B baseline that future base-model candidates (e.g. Phi-4 Mini
# 128K) must clearly beat before a swap, per CLAUDE.md Phase 3.
GOLDEN_QUERIES = [
    {"query": "who am I", "expected_category": "identity"},
    {"query": "tell me about Null", "expected_category": "fiction"},
    {"query": "who is Fatale", "expected_category": "fiction"},
    {"query": "tell me a story", "expected_category": "fiction"},
    {"query": "tell me about the mood garden project", "expected_category": "project"},
    {"query": "what's my trading strategy", "expected_category": "finance"},
    {"query": "how does nova_query.py work", "expected_category": "technical"},
    {"query": "what's a good way to stay productive", "expected_category": "general"},
]


# ── Context-window benchmark (unchanged) ───────────────────────
def test_context_size(num_ctx: int) -> dict:
    """Run a single inference at the given context size and measure it."""
    print(f"  Testing num_ctx={num_ctx}...", end=" ", flush=True)
    start = time.time()
    try:
        response = ollama.chat(
            model=MODEL,
            messages=[{"role": "user", "content": TEST_PROMPT}],
            options={"num_ctx": num_ctx}
        )
        elapsed = time.time() - start
        print(f"✓ {elapsed:.1f}s")
        return {"num_ctx": num_ctx, "success": True, "time": elapsed}
    except Exception as e:
        elapsed = time.time() - start
        print(f"✗ failed ({e})")
        return {"num_ctx": num_ctx, "success": False, "time": elapsed, "error": str(e)}

def run_benchmark():
    print(f"\nNova Context Window Benchmark")
    print(f"Model: {MODEL}")
    print(f"Testing context sizes: {TEST_SIZES}\n")
    print("Results:")

    results = []
    for size in TEST_SIZES:
        result = test_context_size(size)
        results.append(result)
        # If a size fails, larger ones will too — stop early
        if not result["success"]:
            print(f"\n  Stopping — {size} failed, larger sizes won't work.")
            break

    print("\n── Summary ──────────────────────────────────────")
    passing = [r for r in results if r["success"]]
    if not passing:
        print("No context sizes succeeded. Check that Ollama is running.")
        return

    best = max(passing, key=lambda r: r["num_ctx"])
    fastest = min(passing, key=lambda r: r["time"])

    print(f"Maximum supported context: {best['num_ctx']} tokens")
    print(f"Fastest context size: {fastest['num_ctx']} tokens ({fastest['time']:.1f}s)")
    print()

    if best["num_ctx"] >= 8192:
        recommended = 8192
    elif best["num_ctx"] >= 4096:
        recommended = 4096
    else:
        recommended = 2048

    print(f"Recommendation: set num_ctx={recommended} in nova_query.py")
    print(f"  → In nova_query.py, add options={{'num_ctx': {recommended}}} to ollama.chat()")
    print()

    for r in results:
        status = f"✓ {r['time']:.1f}s" if r["success"] else "✗ failed"
        print(f"  {r['num_ctx']:>6} tokens — {status}")


# ── Golden-query RAG benchmark ──────────────────────────────────
def _run_single_golden_query(entry: dict) -> dict:
    """
    Run one golden query through the full RAG pipeline (nova_query.ask),
    timing it and checking routing/blending. Returns the per-query result
    dict recorded in the benchmark log.
    """
    query = entry["query"]
    expected_category = entry["expected_category"]

    start = time.perf_counter()
    result = nova_query.ask(query, persist=False)
    latency_ms = int((time.perf_counter() - start) * 1000)

    actual_category = result["category"]
    blend_detected = detect_blending(result["chunks"], actual_category)

    return {
        "query": query,
        "expected_category": expected_category,
        "actual_category": actual_category,
        "category_match": actual_category == expected_category,
        "latency_ms": latency_ms,
        "blend_detected": blend_detected,
    }


def _aggregate_golden_results(per_query_results: list[dict]) -> dict:
    """
    Roll up per-query golden benchmark results into the summary stats
    written to benchmark_log.jsonl: overall/by-category avg latency,
    fiction-only blend rate, and a count of routing mismatches.
    """
    latencies = [r["latency_ms"] for r in per_query_results]
    avg_latency_ms = round(sum(latencies) / len(latencies))

    avg_latency_ms_by_category = {}
    categories = {r["actual_category"] for r in per_query_results}
    for category in categories:
        category_latencies = [
            r["latency_ms"] for r in per_query_results if r["actual_category"] == category
        ]
        avg_latency_ms_by_category[category] = round(
            sum(category_latencies) / len(category_latencies)
        )

    fiction_results = [r for r in per_query_results if r["expected_category"] == "fiction"]
    if fiction_results:
        blend_rate = round(
            sum(1 for r in fiction_results if r["blend_detected"]) / len(fiction_results), 3
        )
    else:
        blend_rate = None

    category_mismatches = sum(1 for r in per_query_results if not r["category_match"])

    return {
        "avg_latency_ms": avg_latency_ms,
        "avg_latency_ms_by_category": avg_latency_ms_by_category,
        "blend_rate": blend_rate,
        "category_mismatches": category_mismatches,
    }


def _log_golden_benchmark(model_label: str, summary: dict, per_query_results: list[dict]) -> None:
    """
    Append one JSON entry to benchmark_log.jsonl. Mirrors nova_log.py's
    append convention: os.makedirs(..., exist_ok=True), then open in mode
    "a", utf-8.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model_label,
        "avg_latency_ms": summary["avg_latency_ms"],
        "avg_latency_ms_by_category": summary["avg_latency_ms_by_category"],
        "blend_rate": summary["blend_rate"],
        "category_mismatches": summary["category_mismatches"],
        "per_query_results": per_query_results,
    }
    with open(BENCHMARK_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_golden_benchmark(model_label: str = MODEL) -> dict:
    """
    Run every GOLDEN_QUERIES entry through the real RAG pipeline
    (nova_query.ask), aggregate latency/routing/blend stats, append one
    entry to benchmark_log.jsonl, and print a human-readable summary.

    This establishes/re-checks the base-model baseline referenced by
    CLAUDE.md Phase 3's swap trigger — a candidate model must clearly beat
    these numbers before a swap, not just match a fixed timeline.
    """
    print(f"\nNova Golden Query Benchmark")
    print(f"Model: {model_label}")
    print(f"Running {len(GOLDEN_QUERIES)} golden queries...\n")

    per_query_results = []
    for entry in GOLDEN_QUERIES:
        print(f"  Querying: \"{entry['query']}\"...", end=" ", flush=True)
        result = _run_single_golden_query(entry)
        per_query_results.append(result)
        status = "✓" if result["category_match"] else "✗ MISMATCH"
        print(f"{status} ({result['actual_category']}, {result['latency_ms']}ms)")

    summary = _aggregate_golden_results(per_query_results)
    _log_golden_benchmark(model_label, summary, per_query_results)

    print("\n── Summary ──────────────────────────────────────")
    print(f"Model: {model_label}")
    print(f"Average latency: {summary['avg_latency_ms']}ms")
    print("Average latency by category:")
    for category, avg_ms in summary["avg_latency_ms_by_category"].items():
        print(f"  {category:>12} — {avg_ms}ms")

    if summary["blend_rate"] is None:
        print("Blend rate (fiction queries): n/a (no fiction queries)")
    else:
        print(f"Blend rate (fiction queries): {summary['blend_rate']:.1%}")

    if summary["category_mismatches"] > 0:
        print(f"\n⚠ {summary['category_mismatches']} routing mismatch(es) — flagged as a regression:")
        for r in per_query_results:
            if not r["category_match"]:
                print(f"  \"{r['query']}\" — expected {r['expected_category']}, got {r['actual_category']}")
    else:
        print("\nNo routing mismatches.")

    print(f"\nLogged to {BENCHMARK_LOG_PATH}")

    return {
        "model": model_label,
        **summary,
        "per_query_results": per_query_results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nova benchmarking suite")
    parser.add_argument(
        "--golden",
        action="store_true",
        help="Run the golden-query RAG benchmark instead of the context-window benchmark"
    )
    args = parser.parse_args()

    if args.golden:
        run_golden_benchmark()
    else:
        run_benchmark()
