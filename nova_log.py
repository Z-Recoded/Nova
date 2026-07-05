# nova_log.py
# Nova Log — operational telemetry for every real query (query_log.jsonl),
# plus the read/aggregation helpers behind the /nova-log Health dashboard.
# Not to be confused with nova_logger.py, which detects character-blending
# and logs training data.

import json
import os
from datetime import datetime, timedelta

# ── Config ─────────────────────────────────────────────────────
LOGS_DIR = "C:/Nova/logs"
QUERY_LOG_PATH = f"{LOGS_DIR}/query_log.jsonl"

BLEND_RATE_WINDOW = 100     # most-recent entries used to compute blend rate
LATENCY_WINDOW_HOURS = 24   # window used for average latency


# ── Logging ────────────────────────────────────────────────────
def log_query(
    query: str,
    category: str,
    sources: list[str],
    chunks_retrieved: int,
    blend_detected: bool,
    retrieval_ms: int,
    inference_ms: int,
    total_ms: int,
    prompt_tokens: int | None,
    response_tokens: int | None,
    model: str,
    num_ctx: int,
) -> None:
    """
    Append one real query's telemetry to query_log.jsonl.
    Mirrors nova_logger.log_blend's append convention: JSONL (one JSON
    object per line), mode "a", utf-8, creating the logs directory first.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "query": query,
        "category": category,
        "sources": sources,
        "chunks_retrieved": chunks_retrieved,
        "blend_detected": blend_detected,
        "retrieval_ms": retrieval_ms,
        "inference_ms": inference_ms,
        "total_ms": total_ms,
        "prompt_tokens": prompt_tokens,
        "response_tokens": response_tokens,
        "model": model,
        "num_ctx": num_ctx,
    }
    with open(QUERY_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Reading ────────────────────────────────────────────────────
def _read_all_entries() -> list[dict]:
    """Read every line of query_log.jsonl into dicts. Empty list if the file doesn't exist yet."""
    if not os.path.exists(QUERY_LOG_PATH):
        return []
    entries = []
    with open(QUERY_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ── Aggregation ────────────────────────────────────────────────
def compute_health_summary() -> dict:
    """
    Real, currently-available Health stats computed from query_log.jsonl:
    total_queries, avg_latency_ms_24h, blend_rate_last_100, last_query_timestamp.
    Returns gracefully empty values if no queries have been logged yet.
    """
    entries = _read_all_entries()

    if not entries:
        return {
            "total_queries": 0,
            "avg_latency_ms_24h": None,
            "blend_rate_last_100": None,
            "last_query_timestamp": None,
        }

    cutoff = datetime.now() - timedelta(hours=LATENCY_WINDOW_HOURS)
    recent_latencies = [
        e["total_ms"] for e in entries
        if datetime.fromisoformat(e["timestamp"]) >= cutoff
    ]
    avg_latency_ms_24h = (
        round(sum(recent_latencies) / len(recent_latencies)) if recent_latencies else None
    )

    last_n = entries[-BLEND_RATE_WINDOW:]
    blend_rate_last_100 = round(
        sum(1 for e in last_n if e["blend_detected"]) / len(last_n), 3
    )

    return {
        "total_queries": len(entries),
        "avg_latency_ms_24h": avg_latency_ms_24h,
        "blend_rate_last_100": blend_rate_last_100,
        "last_query_timestamp": entries[-1]["timestamp"],
    }
