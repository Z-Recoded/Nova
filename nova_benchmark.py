# nova_benchmark.py
# Tests Ollama context window sizes to find what your machine can handle
# Run this once to determine the best num_ctx setting for nova_query.py

import time
import ollama

MODEL = "llama3.2"
TEST_SIZES = [2048, 4096, 8192, 16384, 32768]

# A prompt that's short but forces the model to reason
TEST_PROMPT = "Summarize what a knowledge base assistant does in two sentences."

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

if __name__ == "__main__":
    run_benchmark()
