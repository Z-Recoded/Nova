# nova_api.py
# Nova FastAPI server
#
# Endpoints:
#   POST /ask                       → RAG query (full pipeline)
#   GET  /graph                     → full node/edge map
#   GET  /neighbors?file=X          → outgoing + incoming edges for X
#   GET  /context-budget?query=X    → ranked file list for a query
#   POST /ingest                    → trigger incremental or full ingest
#   POST /rebuild-node              → rebuild graph node for one file
#   GET  /v1/models                 → OpenAI-compatible model list (Open WebUI)
#   POST /v1/chat/completions       → OpenAI-compatible chat (Open WebUI → RAG pipeline)
#   GET  /nova-log                  → Nova Log Health dashboard (HTML)
#   GET  /nova-log/data             → Nova Log Health dashboard data (JSON)
#   GET  /nova-log/queries          → Nova Log Query view — recent queries (JSON)
#   GET  /nova-log/benchmarks       → Nova Log Benchmark view — recent golden-query runs (JSON)
#   POST /agent/task                → run a coding task via Nova's coding sub-agent
#   GET  /headroom                  → resource headroom report (VRAM/RAM/CPU + task capacity)
#
# Run:
#   cd C:/Nova
#   nova-env\Scripts\uvicorn nova_api:app --host 0.0.0.0 --port 8000 --reload

import json
import os
import time
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from graph_builder import (
    build_graph,
    get_context_budget,
    get_neighbors,
    rebuild_node,
)
from ingest import ingest_file, run_ingestion
from nova_headroom import get_headroom_report
from nova_log import (
    compute_health_summary,
    DEFAULT_BENCHMARK_RUNS_LIMIT,
    DEFAULT_RECENT_QUERIES_LIMIT,
    get_benchmark_runs,
    get_recent_queries,
)
from nova_orchestrator import run_coding_task
from nova_query import ask
from nova_sources import SOURCES

app = FastAPI(title="Nova API", version="0.3")

GRAPH_PATH = "C:/Nova/nova_graph.json"

# Model name shown in Open WebUI's model picker. All requests for this model
# run through Nova's full RAG pipeline — never raw Ollama.
OPENAI_MODEL_ID = "nova"


# ── Helpers ────────────────────────────────────────────────────

def _load_graph_json() -> dict:
    if not os.path.exists(GRAPH_PATH):
        return {"nodes": [], "edges": []}
    try:
        with open(GRAPH_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"nodes": [], "edges": []}


def _resolve_source(filepath: str) -> tuple[str, str]:
    """Find the project + description for a file path from SOURCES config."""
    for src in SOURCES:
        if filepath.startswith(src["path"]):
            return src["project"], src["description"]
    return "Unknown", ""


# ── Request / Response models ──────────────────────────────────

class AskRequest(BaseModel):
    query: str
    history: Optional[list[dict]] = None
    persist: bool = True


class IngestRequest(BaseModel):
    full: bool = False


class RebuildNodeRequest(BaseModel):
    filepath: str


class AgentTaskRequest(BaseModel):
    task: str
    category: str | None = None


# ── Routes ─────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "Nova API online", "version": "0.3"}


@app.post("/ask")
def ask_nova(req: AskRequest):
    """
    Full RAG pipeline.
    Returns answer, sources, category, and chunk metadata.
    """
    try:
        result = ask(
            query=req.query,
            history=req.history or [],
            persist=req.persist,
        )
        return {
            "answer": result["answer"],
            "sources": result["sources"],
            "category": result["category"],
            "chunks": [
                {
                    "text": c["text"],
                    "filename": c["metadata"].get("filename", ""),
                    "project": c["metadata"].get("project", ""),
                    "distance": c["distance"],
                }
                for c in result["chunks"]
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/graph")
def get_graph():
    """Return the full node/edge map from nova_graph.json."""
    graph = _load_graph_json()
    if not graph["nodes"]:
        raise HTTPException(
            status_code=404,
            detail="Graph not built yet. POST /ingest or run graph_builder.py first.",
        )
    return graph


@app.get("/neighbors")
def neighbors(file: str = Query(..., description="Filename, e.g. 'My Note.md'")):
    """
    Return all outgoing and incoming edges for a given file.
    """
    result = get_neighbors(file)
    if not result["outgoing"] and not result["incoming"]:
        # Check if the file even exists in the graph
        graph = _load_graph_json()
        known = {n["id"] for n in graph["nodes"]}
        if file not in known:
            raise HTTPException(status_code=404, detail=f"File '{file}' not found in graph.")
    return result


@app.get("/context-budget")
def context_budget(
    query: str = Query(..., description="Natural language query"),
    n_seeds: int = Query(8, ge=1, le=30),
    n_files: int = Query(15, ge=1, le=50),
    max_hops: int = Query(2, ge=0, le=4),
):
    """
    Return a ranked list of filenames most relevant to `query`,
    combining semantic search seeds with graph proximity expansion.
    """
    ranked = get_context_budget(query, n_seeds=n_seeds, n_files=n_files, max_hops=max_hops)
    return {"query": query, "files": ranked, "count": len(ranked)}


@app.post("/ingest")
def trigger_ingest(req: IngestRequest):
    """Trigger incremental (default) or full re-ingest."""
    try:
        run_ingestion(full=req.full)
        # Rebuild the graph after ingest
        graph = build_graph()
        return {
            "status": "ok",
            "mode": "full" if req.full else "incremental",
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(graph["edges"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── OpenAI-compatible routes (Open WebUI) ──────────────────────

def _split_openai_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """
    Split an OpenAI-style messages array into (query, history) for ask().

    Drops system messages — Nova builds its own system prompt inside
    nova_query.build_system_prompt(). The last user message becomes the
    query; everything before it becomes the history, which ask() already
    accepts in this same role/content dict format.
    """
    chat_messages = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not chat_messages or chat_messages[-1].get("role") != "user":
        raise HTTPException(status_code=400, detail="Last message must be from the user.")

    query = chat_messages[-1]["content"]
    history = chat_messages[:-1]
    return query, history


def _append_sources_footer(answer: str, sources: list[str]) -> str:
    """
    Append a markdown footer listing the retrieved source files, so every
    Open WebUI response shows which notes grounded the answer.
    """
    if not sources:
        return answer
    return answer + "\n\n---\n**Sources:** " + ", ".join(sorted(sources))


def _build_completion_response(content: str, model: str) -> dict:
    """Build a non-streaming OpenAI chat.completion response body."""
    return {
        "id": f"nova-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _stream_completion_response(content: str, model: str) -> StreamingResponse:
    """
    Stream the answer as OpenAI-style server-sent events.

    The answer is already fully generated by the time we respond (ask() is
    blocking), so we emit it as a single content delta followed by a finish
    chunk and [DONE] — the simplest shape Open WebUI accepts.
    """
    completion_id = f"nova-{uuid.uuid4()}"
    created = int(time.time())

    def make_chunk(delta: dict, finish_reason: Optional[str]) -> str:
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(chunk)}\n\n"

    def event_stream():
        yield make_chunk({"role": "assistant", "content": content}, finish_reason=None)
        yield make_chunk({}, finish_reason="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/v1/models")
def list_models():
    """OpenAI-compatible model list. Open WebUI calls this to fill its model picker."""
    return {
        "object": "list",
        "data": [{
            "id": OPENAI_MODEL_ID,
            "object": "model",
            "created": int(time.time()),
            "owned_by": "nova",
        }],
    }


@app.post("/v1/chat/completions")
def openai_chat_completions(body: dict):
    """
    OpenAI-compatible chat endpoint for Open WebUI.

    Runs the last user message through Nova's full RAG pipeline via ask(),
    with the prior turns passed as history. persist=False because Open WebUI
    resends the whole conversation every turn and keeps its own history —
    persisting here would double-write Nova's memory store.
    """
    try:
        query, history = _split_openai_messages(body.get("messages", []))
        model = body.get("model", OPENAI_MODEL_ID)
        wants_stream = bool(body.get("stream", False))

        result = ask(query=query, history=history, persist=False)
        content = _append_sources_footer(result["answer"], result["sources"])

        if wants_stream:
            return _stream_completion_response(content, model)
        return _build_completion_response(content, model)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/agent/task")
def agent_task(req: AgentTaskRequest):
    """
    Run a coding task through Nova's coding sub-agent (nova_orchestrator.py).

    The task runs in a disposable git worktree — nothing touches the live
    Nova codebase. Returns the branch name and diff for human review; never
    auto-merges. `category` optionally selects a Nova Skills Library file
    (coding/retrieval/financial/orchestration/lore/memory) to prepend to
    the task's context — no effect unless skill_injection is enabled in
    nova_config.json.
    """
    try:
        return run_coding_task(req.task, category=req.category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rebuild-node")
def trigger_rebuild_node(req: RebuildNodeRequest):
    """Rebuild the graph node for a single file (used by nova_watcher)."""
    try:
        graph = rebuild_node(req.filepath)
        filename = os.path.basename(req.filepath)
        node = next((n for n in graph["nodes"] if n["id"] == filename), None)
        edges_out = [e for e in graph["edges"] if e["source"] == filename]
        return {
            "status": "ok",
            "file": filename,
            "chunk_count": node["chunk_count"] if node else 0,
            "outgoing_edges": len(edges_out),
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(graph["edges"]),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Resource headroom (Phase 1.5 self-monitoring) ───────────────

@app.get("/headroom")
def headroom():
    """
    Return Nova's current resource headroom report — VRAM (nvidia-smi), RAM
    + CPU (psutil), ingestion queue depth, active session count, and a
    plain-English summary of how many heavy/medium/light tasks Nova could
    still take on before hitting nominal load thresholds.
    """
    try:
        return get_headroom_report()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Nova Log — Health dashboard ────────────────────────────────

# Health-view fields the spec calls for that have no real data source yet.
# Surfaced honestly with a reason each, rather than faked or silently omitted —
# see the Nova Log v1 plan for why each one is deferred.
NOVA_LOG_UNAVAILABLE_FIELDS = {
    "retrieval_hit_rate": "Not tracked yet — no ground-truth relevance labels to compare against.",
    "active_augments": "No augment/config-flag system exists in current Nova (single model, no toggles).",
    "orchestrator_failures_count": "nova_orchestrator.py exists (coding sub-agent) but doesn't track an aggregate failure count yet.",
}

NOVA_LOG_HTML_PATH = "C:/Nova/nova_log.html"


@app.get("/nova-log/data")
def nova_log_data():
    """JSON data backing the /nova-log Health dashboard."""
    summary = compute_health_summary()
    latest_benchmarks = get_benchmark_runs(limit=1)
    summary["last_benchmark_run"] = latest_benchmarks[0]["timestamp"] if latest_benchmarks else None
    summary["not_yet_available"] = NOVA_LOG_UNAVAILABLE_FIELDS
    return summary


@app.get("/nova-log/queries")
def nova_log_queries(
    limit: int = Query(DEFAULT_RECENT_QUERIES_LIMIT, ge=1, le=1000),
    category: Optional[str] = Query(None, description="Filter to an exact category match"),
    model: Optional[str] = Query(None, description="Filter to an exact model match"),
    blend_detected: Optional[bool] = Query(None, description="Filter to blend_detected true/false"),
):
    """
    Nova Log Query view — the last `limit` real queries (most recent first),
    optionally filtered by category, model, and/or blend_detected.
    """
    try:
        queries = get_recent_queries(
            limit=limit,
            category=category,
            model=model,
            blend_detected=blend_detected,
        )
        return {"queries": queries, "count": len(queries)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/nova-log/benchmarks")
def nova_log_benchmarks(
    limit: int = Query(DEFAULT_BENCHMARK_RUNS_LIMIT, ge=1, le=1000),
    model: Optional[str] = Query(None, description="Filter to an exact model match"),
):
    """
    Nova Log Benchmark view — the last `limit` golden-query benchmark runs
    from benchmark_log.jsonl (most recent first), optionally filtered by model.
    """
    try:
        runs = get_benchmark_runs(limit=limit, model=model)
        return {"runs": runs, "count": len(runs)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/nova-log")
def nova_log_page():
    """Serve the Nova Log Health dashboard — static HTML/JS, fetches /nova-log/data."""
    return FileResponse(NOVA_LOG_HTML_PATH, media_type="text/html")
