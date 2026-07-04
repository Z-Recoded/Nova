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
#
# Run:
#   cd C:/Nova
#   nova-env\Scripts\uvicorn nova_api:app --host 0.0.0.0 --port 8000 --reload

import json
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from graph_builder import (
    build_graph,
    get_context_budget,
    get_neighbors,
    rebuild_node,
)
from ingest import ingest_file, run_ingestion
from nova_query import ask
from nova_sources import SOURCES

app = FastAPI(title="Nova API", version="0.3")

GRAPH_PATH = "C:/Nova/nova_graph.json"


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
