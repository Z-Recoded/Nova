# nova_mcp_server.py
# Nova MCP Server — exposes Nova's existing FastAPI routes as MCP tools.
#
# This server does NOT talk to Chroma, Ollama, or any RAG/graph/ingest logic
# directly. It is a thin HTTP client layer: every tool below makes an httpx
# call to nova_api.py (which must already be running separately on port 8000)
# and returns the parsed JSON response. This preserves the project's Golden
# Rule — FastAPI is the only interface other components talk to.
#
# Tools registered (each wraps exactly one nova_api.py route):
#   - nova_query(query, history)                          -> POST /ask
#   - nova_graph()                                        -> GET  /graph
#   - nova_neighbors(file)                                -> GET  /neighbors
#   - nova_context_budget(query, n_seeds, n_files, max_hops) -> GET /context-budget
#   - nova_ingest(full)                                   -> POST /ingest
#
# Run standalone with:
#   python nova_mcp_server.py
# which serves MCP over streamable-http on port 8100 (deliberately not 8000,
# so it never collides with nova_api.py).

import httpx
from mcp.server.fastmcp import FastMCP

# ── Constants / config ──

# Base URL of the already-running Nova FastAPI server (nova_api.py).
NOVA_API_BASE_URL = "http://localhost:8000"

# Port this MCP server listens on for streamable-http transport.
# Deliberately different from nova_api.py's port 8000.
MCP_SERVER_PORT = 8100

# Timeout (seconds) for most HTTP calls to nova_api.py.
DEFAULT_TIMEOUT_SECONDS = 30

# Longer timeout (seconds) for /ask specifically, since full RAG retrieval +
# generation is slower than the other routes.
QUERY_TIMEOUT_SECONDS = 120

# ── Setup ──

app = FastMCP("nova", port=MCP_SERVER_PORT)


# ── Helper functions ──

def _raise_for_request_failure(route: str, error: Exception) -> None:
    """
    Turns an httpx exception into a clear RuntimeError naming the failed
    route and the underlying reason. Exists so every tool below reports
    failures the same, legible way instead of silently returning nothing.
    """
    raise RuntimeError(f"Call to Nova API route '{route}' failed: {error}") from error


# ── Core tools ──

@app.tool()
def nova_query(query: str, history: list[dict] | None = None) -> dict:
    """
    Runs a full RAG query through Nova's /ask route (retrieve + generate).
    `history` is prior conversation turns, passed through unchanged; if not
    given, an empty history is sent. Persists the turn to conversation
    history on the Nova API side (persist=true), matching normal chat usage.
    """
    request_body = {
        "query": query,
        "history": history or [],
        "persist": True,
    }

    try:
        response = httpx.post(
            f"{NOVA_API_BASE_URL}/ask",
            json=request_body,
            timeout=QUERY_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        _raise_for_request_failure("/ask", error)


@app.tool()
def nova_graph() -> dict:
    """
    Fetches the full wikilink graph (nodes + edges) from Nova's /graph route.
    Used for graph exploration without talking to nova_graph.json directly.
    """
    try:
        response = httpx.get(
            f"{NOVA_API_BASE_URL}/graph",
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        _raise_for_request_failure("/graph", error)


@app.tool()
def nova_neighbors(file: str) -> dict:
    """
    Fetches incoming + outgoing wikilink edges for a single file via Nova's
    /neighbors route. `file` should match a node id in nova_graph.json
    (e.g. "Null.md").
    """
    try:
        response = httpx.get(
            f"{NOVA_API_BASE_URL}/neighbors",
            params={"file": file},
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        _raise_for_request_failure("/neighbors", error)


@app.tool()
def nova_context_budget(
    query: str,
    n_seeds: int = 8,
    n_files: int = 15,
    max_hops: int = 2,
) -> dict:
    """
    Fetches a ranked list of relevant files from Nova's /context-budget
    route (graph-guided seed search). Used to see which files graph-guided
    retrieval would scope a query to, without running full generation.
    """
    try:
        response = httpx.get(
            f"{NOVA_API_BASE_URL}/context-budget",
            params={
                "query": query,
                "n_seeds": n_seeds,
                "n_files": n_files,
                "max_hops": max_hops,
            },
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        _raise_for_request_failure("/context-budget", error)


@app.tool()
def nova_ingest(full: bool = False) -> dict:
    """
    Triggers an ingest run via Nova's /ingest route. `full=False` (default)
    requests an incremental ingest; `full=True` requests a full re-ingest.
    """
    request_body = {"full": full}

    try:
        response = httpx.post(
            f"{NOVA_API_BASE_URL}/ingest",
            json=request_body,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as error:
        _raise_for_request_failure("/ingest", error)


# ── Main / entry point ──

if __name__ == "__main__":
    # Serve MCP over streamable-http on MCP_SERVER_PORT (8100), distinct from
    # nova_api.py's port 8000, so both servers can run at the same time.
    app.run(transport="streamable-http")
