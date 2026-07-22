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
import subprocess
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
def _run_single_golden_query(entry: dict, model: str) -> dict:
    """
    Run one golden query through the full RAG pipeline (nova_query.ask),
    timing it and checking routing/blending. Returns the per-query result
    dict recorded in the benchmark log.

    `model` is passed as nova_query.ask()'s model_override, so the query
    actually runs on this model — previously this function only ever ran
    the query on nova_query.py's own hardcoded OLLAMA_MODEL regardless of
    what model_label the caller intended to benchmark.
    """
    query = entry["query"]
    expected_category = entry["expected_category"]

    start = time.perf_counter()
    result = nova_query.ask(query, persist=False, model_override=model)
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

    `model_label` now genuinely controls which model generates every answer
    (via nova_query.ask()'s model_override), not just the label recorded in
    benchmark_log.jsonl — see _run_single_golden_query.
    """
    print(f"\nNova Golden Query Benchmark")
    print(f"Model: {model_label}")
    print(f"Running {len(GOLDEN_QUERIES)} golden queries...\n")

    per_query_results = []
    for entry in GOLDEN_QUERIES:
        print(f"  Querying: \"{entry['query']}\"...", end=" ", flush=True)
        result = _run_single_golden_query(entry, model=model_label)
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


# ── Model-swap candidate evaluator ──────────────────────────────
def _get_latest_baseline_entry(baseline_model: str) -> dict | None:
    """
    Read benchmark_log.jsonl and return the most recent entry whose "model"
    field matches baseline_model (nova_query.OLLAMA_MODEL — today's deployed
    model), or None if no such entry exists yet. Used to compare a candidate
    against the real, currently-logged baseline rather than a guess.
    """
    if not os.path.exists(BENCHMARK_LOG_PATH):
        return None
    latest = None
    with open(BENCHMARK_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("model") == baseline_model:
                latest = entry
    return latest


def evaluate_candidate(candidate_model: str) -> dict:
    """
    One-command model-swap check: pull the candidate, run the golden
    benchmark on it for real, and compare against the most recent logged
    baseline for nova_query.py's current deployed model.

    Pass/fail rule (this function's own operationalization of CLAUDE.md
    Phase 3's "must clearly beat the baseline" — the spec doesn't quantify
    a threshold): PASS only if the candidate is not worse than baseline on
    every metric (avg_latency_ms, blend_rate, category_mismatches) AND
    strictly better on at least one. Every metric's raw delta is always
    printed alongside the verdict, so the call is never a black box.
    """
    baseline_model = nova_query.OLLAMA_MODEL

    print(f"\nPulling candidate model: {candidate_model}...")
    # encoding="utf-8", errors="replace": ollama pull's terminal output (progress
    # bars, etc.) can contain bytes Windows' default cp1252 console codepage
    # can't decode — same class of bug already hit and fixed elsewhere in this
    # file (see the sys.stdout.reconfigure call above) and in nova_orchestrator.py's
    # git subprocess calls.
    pull_result = subprocess.run(
        ["ollama", "pull", candidate_model], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if pull_result.returncode != 0:
        print(f"✗ Failed to pull {candidate_model}: {pull_result.stderr}")
        return {"candidate_model": candidate_model, "pulled": False, "error": pull_result.stderr}

    candidate_summary = run_golden_benchmark(model_label=candidate_model)
    baseline_entry = _get_latest_baseline_entry(baseline_model)

    if baseline_entry is None:
        print(
            f"\nNo logged baseline found for '{baseline_model}' yet — run "
            f"`nova_benchmark.py --golden` against it first to establish one. "
            f"Skipping comparison."
        )
        return {"candidate_model": candidate_model, "pulled": True, "baseline_found": False,
                 "candidate_summary": candidate_summary}

    # All three metrics are "lower is better" (latency, blend rate, mismatches).
    metric_names = ["avg_latency_ms", "blend_rate", "category_mismatches"]
    comparisons = {}
    not_worse_on_all = True
    strictly_better_on_one = False
    for metric_name in metric_names:
        candidate_value = candidate_summary.get(metric_name)
        baseline_value = baseline_entry.get(metric_name)
        if candidate_value is None or baseline_value is None:
            comparisons[metric_name] = {"candidate": candidate_value, "baseline": baseline_value, "delta": None}
            continue
        delta = candidate_value - baseline_value
        comparisons[metric_name] = {"candidate": candidate_value, "baseline": baseline_value, "delta": delta}
        if delta > 0:
            not_worse_on_all = False
        elif delta < 0:
            strictly_better_on_one = True

    passed = not_worse_on_all and strictly_better_on_one

    print(f"\n── Candidate vs. baseline ({baseline_model}) ──────────────")
    for metric_name, comparison in comparisons.items():
        delta = comparison["delta"]
        delta_str = "n/a" if delta is None else f"{delta:+g}"
        print(f"  {metric_name:>22} — candidate {comparison['candidate']}, baseline {comparison['baseline']} (Δ {delta_str})")

    verdict = "PASS — clearly beats baseline" if passed else "FAIL — does not clearly beat baseline"
    print(f"\nVerdict: {verdict}")

    return {
        "candidate_model": candidate_model,
        "baseline_model": baseline_model,
        "pulled": True,
        "baseline_found": True,
        "comparisons": comparisons,
        "passed": passed,
    }


# ── Context-fill + cold-start benchmark (Phi-4 Mini 128K validation, 86bagek35) ─
# Distinct from test_context_size() above: that function only allocates an
# empty num_ctx buffer and times a short prompt (tests whether the size can be
# allocated at all). This fills the context with real content up to each
# target size, so it measures what 86bagek35 actually asks for — "context
# fill latency" — using Phi-4 Mini's real tokenizer for an exact token count
# rather than a char-count guess.
CONTEXT_FILL_SIZES = [8192, 32768, 65536, 131072]
CONTEXT_FILL_TOKENIZER_ID = "unsloth/Phi-4-mini-instruct-bnb-4bit"
CONTEXT_FILL_INSTRUCTION = "\n\nSummarize the passage above in exactly one sentence."
_FILLER_PARAGRAPH = (
    "The quick brown fox jumps over the lazy dog near the riverbank at dawn, "
    "while the town slowly wakes to the sound of church bells and distant traffic. "
)


def _build_context_fill_prompt(tokenizer, target_tokens: int) -> str:
    """
    Repeat the filler paragraph until the tokenized prompt reaches
    target_tokens, then append a one-line instruction — the model has to
    attend across the whole filled context to answer, not just the tail.
    """
    instruction_tokens = len(tokenizer.encode(CONTEXT_FILL_INSTRUCTION))
    budget = target_tokens - instruction_tokens
    text = ""
    while len(tokenizer.encode(text)) < budget:
        text += _FILLER_PARAGRAPH
    return text + CONTEXT_FILL_INSTRUCTION


def test_context_fill(model: str, target_tokens: int, tokenizer) -> dict:
    """Fill the context to target_tokens (real token count) and time one generation."""
    prompt = _build_context_fill_prompt(tokenizer, target_tokens)
    actual_tokens = len(tokenizer.encode(prompt))
    print(f"  Filling to ~{target_tokens} tokens (actual: {actual_tokens})...", end=" ", flush=True)
    start = time.time()
    try:
        # nova_query.ollama_client, not bare ollama.chat() -- guards against the
        # OLLAMA_HOST=0.0.0.0 bind-all gotcha (see nova_query.py's own comment),
        # which the module-level ollama.chat() used by test_context_size() above
        # doesn't handle.
        nova_query.ollama_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": target_tokens},
        )
        elapsed = time.time() - start
        print(f"✓ {elapsed:.1f}s")
        return {"target_tokens": target_tokens, "actual_tokens": actual_tokens, "success": True, "time": elapsed}
    except Exception as e:
        elapsed = time.time() - start
        print(f"✗ failed ({e})")
        return {"target_tokens": target_tokens, "actual_tokens": actual_tokens, "success": False,
                "time": elapsed, "error": str(e)}


def run_context_fill_benchmark(model: str) -> list[dict]:
    """
    Run the context-fill latency sweep (8K/32K/64K/128K, 86bagek35's own
    stated targets) for `model` and print a summary table. Note: token
    counts use Phi-4 Mini's Hugging Face tokenizer as a real, exact proxy —
    Ollama's bundled GGUF tokenizer should match, but isn't loaded directly.
    """
    from transformers import AutoTokenizer

    print(f"\nLoading tokenizer ({CONTEXT_FILL_TOKENIZER_ID})...")
    tokenizer = AutoTokenizer.from_pretrained(CONTEXT_FILL_TOKENIZER_ID)

    print(f"\nNova Context-Fill Latency Benchmark")
    print(f"Model: {model}")
    print(f"Testing fill sizes: {CONTEXT_FILL_SIZES}\n")

    results = []
    for size in CONTEXT_FILL_SIZES:
        result = test_context_fill(model, size, tokenizer)
        results.append(result)
        if not result["success"]:
            print(f"\n  Stopping — {size} failed, larger sizes won't work.")
            break

    print("\n── Summary ──────────────────────────────────────")
    for r in results:
        status = f"✓ {r['time']:.1f}s" if r["success"] else "✗ failed"
        print(f"  ~{r['target_tokens']:>6} tokens (actual {r['actual_tokens']}) — {status}")

    return results


def test_cold_start(model: str) -> dict:
    """
    Unload `model` from memory (ollama stop), then time a trivial request —
    the real load-from-disk-to-first-response cost, distinct from every
    other timing in this file, which assumes the model is already warm.
    """
    print(f"\nUnloading {model}...", end=" ", flush=True)
    subprocess.run(["ollama", "stop", model], capture_output=True, text=True)
    print("done.")

    print(f"Timing cold start for {model}...", end=" ", flush=True)
    start = time.time()
    try:
        nova_query.ollama_client.chat(model=model, messages=[{"role": "user", "content": "hi"}])
        elapsed = time.time() - start
        print(f"✓ {elapsed:.1f}s")
        return {"model": model, "success": True, "cold_start_s": elapsed}
    except Exception as e:
        elapsed = time.time() - start
        print(f"✗ failed ({e})")
        return {"model": model, "success": False, "cold_start_s": elapsed, "error": str(e)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nova benchmarking suite")
    parser.add_argument(
        "--golden",
        action="store_true",
        help="Run the golden-query RAG benchmark instead of the context-window benchmark"
    )
    parser.add_argument(
        "--evaluate",
        metavar="MODEL",
        help="One-command model-swap check: pull MODEL, run the golden benchmark on it, "
             "and compare against the current logged baseline (e.g. --evaluate llama3.1:8b)"
    )
    parser.add_argument(
        "--context-fill",
        metavar="MODEL",
        help="Run the context-fill latency sweep (8K/32K/64K/128K, real filled content) for MODEL"
    )
    parser.add_argument(
        "--cold-start",
        metavar="MODEL",
        help="Unload MODEL then time a cold-start request"
    )
    args = parser.parse_args()

    if args.evaluate:
        evaluate_candidate(args.evaluate)
    elif args.context_fill:
        run_context_fill_benchmark(args.context_fill)
    elif args.cold_start:
        test_cold_start(args.cold_start)
    elif args.golden:
        run_golden_benchmark()
    else:
        run_benchmark()
