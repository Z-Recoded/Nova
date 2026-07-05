# nova_query.py
# Nova retrieval + generation layer
# Queries Chroma memory, builds context, calls local Ollama model

import re
import time

import chromadb
from chromadb.utils import embedding_functions
from datetime import datetime
import ollama

from nova_router import route
from nova_logger import detect_blending, log_blend
from nova_log import log_query
from nova_memory_store import load_history, save_history
from graph_builder import get_context_budget

# ── Config ─────────────────────────────────────────────────────
OLLAMA_MODEL = "llama3.2"
NUM_CTX = 8192  # set based on nova_benchmark.py results
PROFILE_PATH = "C:/Users/marvi/OneDrive/Documents/Second Brain/marvin_profile.md"

# ── Setup ──────────────────────────────────────────────────────
chroma_client = chromadb.PersistentClient(path="C:/Nova/memory")
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="nova_memory",
    embedding_function=embedding_fn
)


# ── Character name → filename map (for per-character retrieval filtering) ──
# Maps each recognized name to its exact, case-preserved source filename.
# Chroma's metadata "filename" field keeps the original filesystem casing
# (see ingest.py), so this must match that casing exactly for the $eq
# filter below to hit anything.
CHARACTER_FILES = {
    "null": "Null.md",
    "nullius": "Nullius.md",
    "helel": "Helel.md",
    "raven": "Raven.md",
    "fatale": "Fatale Wildman.md",
    "luci": "Luci.md",
    "varas": "Varas.md",
    "aseir": "Aseir.md",
    "beat": "Beat.md",
    "rhythm": "Rhythm.md",
    "felicity": "Felicity Malik.md",
    "marisol": "Marisol.md",
    "kille": "Kille & Null.md",
    "symphony": "SYS_Symphony.EXE.md",
    "sys_symphony": "SYS_Symphony.EXE.md",
}

# ── Profile ────────────────────────────────────────────────────
def load_profile() -> str:
    """Always load marvin_profile.md as pinned context."""
    try:
        with open(PROFILE_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ""

# ── System prompt ──────────────────────────────────────────────
def build_system_prompt(route_note: str = "") -> str:
    now = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")
    note_block = f"\nQuery context: {route_note}" if route_note else ""
    return f"""You are Nova — an intelligent assistant with direct access to Marvin's personal knowledge base, projects, notes, and memory.

Current date and time: {now}{note_block}

Rules:
- Answer directly and concisely. No poetry, no philosophy unless explicitly asked.
- For factual questions (time, date, simple facts), answer them plainly and immediately.
- When context from memory is relevant, use it specifically — reference the actual notes, projects, or ideas by name.
- If the retrieved context is not relevant to the question, ignore it and answer from what you know.
- If you genuinely don't know something, say so in one sentence.
- Never add meta-commentary like "This response draws upon..." or "This refers to context from...".
- Never ask rhetorical questions back at Marvin unless he asks for reflection.
- Each memory block is labeled [Source: filename]. Treat each source as a separate document. Never transfer attributes, traits, or facts from one source to another. If two sources describe similar things, keep them distinct and attribute each fact to its source."""

# ── Retrieval ──────────────────────────────────────────────────
def retrieve(query: str, n_results: int = 5, where: dict = None) -> list[dict]:
    """Query Chroma and return top matching chunks with metadata."""
    kwargs = dict(query_texts=[query], n_results=n_results)
    if where:
        kwargs["where"] = where
    results = collection.query(**kwargs)
    chunks = []
    for i in range(len(results["documents"][0])):
        chunks.append({
            "text": results["documents"][0][i],
            "metadata": results["metadatas"][0][i],
            "distance": results["distances"][0][i],
        })
    return chunks


def retrieve_with_graph(query: str, n_results: int = 5, where: dict = None) -> list[dict]:
    """
    Graph-scoped retrieval.

    1. Call get_context_budget() to get a ranked list of relevant files.
    2. Scope the Chroma query to those files using a $in filter on 'filename'.
    3. If the budget is empty or the filtered search returns nothing, fall back
       to the standard unfiltered retrieve().

    The `where` arg from the caller (e.g. character file filter) is AND-ed
    with the budget filter when both are present.
    """
    budget_files = get_context_budget(query, n_seeds=8, n_files=15)

    if budget_files:
        budget_filter = {"filename": {"$in": budget_files}}

        # Merge with any caller-supplied filter
        if where:
            combined_where = {"$and": [budget_filter, where]}
        else:
            combined_where = budget_filter

        chunks = retrieve(query, n_results=n_results, where=combined_where)

        # Fall back to unfiltered (or original filter) if scoped search is empty
        if chunks:
            return chunks

    # Fallback — use caller's original filter (or no filter)
    return retrieve(query, n_results=n_results, where=where)

def build_retrieval_query(query: str, history: list[dict]) -> str:
    """Expand query with recent user turns for better follow-up retrieval."""
    if not history or len(history) < 2:
        return query
    recent = [msg["content"] for msg in history[-4:] if msg["role"] == "user"]
    return " ".join(recent[-2:]) + " " + query

def format_context(chunks: list[dict]) -> str:
    parts = []
    for chunk in chunks:
        meta = chunk["metadata"]
        source = meta.get("filename", "unknown")
        project = meta.get("project", "")
        parts.append(f"[Source: {source} | Project: {project}]\n{chunk['text']}")
    return "\n\n---\n\n".join(parts)

# ── Generation ─────────────────────────────────────────────────
def ask(query: str, history: list[dict] = None, persist: bool = True) -> dict:
    """
    Full RAG pipeline: route → retrieve → generate.
    Returns dict with 'answer', 'sources', 'chunks', 'category'.

    history: list of {"role": "user"|"assistant", "content": "..."} dicts
    persist: if True, saves updated history to disk after responding
    """
    if history is None:
        history = []

    pipeline_start = time.perf_counter()

    # Route the query
    route_result = route(query)

    # Retrieve relevant chunks
    n_results = 3 if route_result.category == "fiction" else route_result.n_results

    retrieval_start = time.perf_counter()
    if route_result.category == "fiction":
        # Don't expand with history — prior character names corrupt the retrieval query
        retrieval_query = query
        # Filter to the named character's file if one is detected.
        # Word-boundary match avoids "null" incorrectly matching inside "nullius".
        q_lower = query.lower()
        char_filter = None
        for name, filename in CHARACTER_FILES.items():
            if re.search(rf"\b{re.escape(name)}\b", q_lower):
                char_filter = {"filename": {"$eq": filename}}
                break
        # Use graph-scoped retrieval; char_filter is merged inside retrieve_with_graph.
        # A hard character filter ($eq) overrides budget scoping for precision.
        if char_filter:
            chunks = retrieve(retrieval_query, n_results=n_results, where=char_filter)
            if not chunks:
                chunks = retrieve(retrieval_query, n_results=n_results)
        else:
            chunks = retrieve_with_graph(retrieval_query, n_results=n_results)
    else:
        retrieval_query = build_retrieval_query(query, history)
        chunks = retrieve_with_graph(retrieval_query, n_results=n_results)
    retrieval_ms = int((time.perf_counter() - retrieval_start) * 1000)
    context = format_context(chunks)

    # Pin Marvin's profile at top of context
    profile = load_profile()
    pinned = f"[PINNED — Marvin's Profile]\n{profile}\n\n---\n\n" if profile else ""

    # Build messages
    messages = [{"role": "system", "content": build_system_prompt(route_result.note)}]
    messages.extend(history)
    messages.append({
        "role": "user",
        "content": f"Here is relevant context from your memory:\n\n{pinned}{context}\n\n---\n\nQuestion: {query}"
    })

    inference_start = time.perf_counter()
    response = ollama.chat(
        model=OLLAMA_MODEL,
        messages=messages,
        options={"num_ctx": NUM_CTX}
    )
    inference_ms = int((time.perf_counter() - inference_start) * 1000)

    answer = response["message"]["content"]

    # Log blending inconsistencies as training material
    blend_detected = detect_blending(chunks, route_result.category)
    if blend_detected:
        log_blend(query, answer, chunks, route_result.category)
    sources = list({c["metadata"].get("filename", "unknown") for c in chunks})

    # Log operational telemetry for the Nova Log Health dashboard — unconditional
    # on persist, since persist only controls history.json, not whether a query
    # happened (Open WebUI's /v1/chat/completions calls ask() with persist=False).
    total_ms = int((time.perf_counter() - pipeline_start) * 1000)
    log_query(
        query=query,
        category=route_result.category,
        sources=sources,
        chunks_retrieved=len(chunks),
        blend_detected=blend_detected,
        retrieval_ms=retrieval_ms,
        inference_ms=inference_ms,
        total_ms=total_ms,
        prompt_tokens=response.get("prompt_eval_count"),
        response_tokens=response.get("eval_count"),
        model=OLLAMA_MODEL,
        num_ctx=NUM_CTX,
    )

    # Persist updated history
    if persist:
        updated = history + [
            {"role": "user", "content": query},
            {"role": "assistant", "content": answer}
        ]
        save_history(updated)

    return {
        "answer": answer,
        "sources": sources,
        "chunks": chunks,
        "category": route_result.category,
    }

# ── Quick test ─────────────────────────────────────────────────
if __name__ == "__main__":
    result = ask("What is the Nova project?")
    print(result["answer"])
    print(f"\nSources: {', '.join(result['sources'])}")
    print(f"Category: {result['category']}")
