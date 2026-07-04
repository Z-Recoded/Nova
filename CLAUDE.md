# CLAUDE.md — Nova Project Context & Coding Standards
> Read this fully before writing a single line. This file is the source of truth for how we work.

---

## 1. Project Overview

Nova is a personal persistent AI system built around Marvin's Second Brain (Obsidian vault). It
is a local-first RAG system with a knowledge graph layer, FastAPI backend, and a training
pipeline for fine-tuning. Nova is not a product — it is a personal tool built incrementally,
one stable phase at a time.

### Current Phase: Phase 1 — Memory Core (active)
Nova v0.1 is operational. The following are built and validated:
- `ingest.py` — document ingestion pipeline into Chroma
- `nova_query.py` — RAG retrieval + generation via Ollama (LLaMA 3.2 3B)
- `nova_router.py` — query routing by category
- `nova_api.py` — FastAPI server (all routes operational except /context-budget — see Section 5)
- `graph_builder.py` — wikilink graph builder, outputs `nova_graph.json`
- `nova_watcher.py` — watchdog file monitor (built, deferred — not running)
- `nova_logger.py` — auto-detects character blending, logs to training_flags.jsonl
- `nova_corrector.py` — reads flags, generates DPO training pairs via Claude API
- `nova_chat.py` — CLI chat interface
- `nova_memory_store.py` — conversation history persistence
- `nova_benchmark.py` — performance benchmarking

### Phase Roadmap
- Phase 0   | Foundation          | ✓ Complete
- Phase 1   | Memory Core         | ✓ Operational — Web UI and Nova Log still needed
- Phase 1.5 | Self-Monitoring     | Backlog — resource headroom calculator
- Phase 2   | Voice & Capture     | Backlog — Whisper + Piper
- Phase 2.5 | Agent Layer         | Backlog — file CRUD, MCP tool-calling, Nova MCP Server
- Phase 3   | First Fine-Tune     | Backlog — Unsloth + DPO → GGUF → Ollama
- Phase 4   | Roaming Layer       | Backlog — VPN, thin client, universal presence
- Phase 5   | Continuous Learning | Backlog — quarterly fine-tune cycles

**Do not build Phase 2+ features without explicit instruction.**

---

## 2. Architecture — Read Before Touching Anything

### The Golden Rule
**FastAPI is the ONLY interface other components talk to.** Nothing talks directly to Chroma
or Ollama except the scripts that are explicitly designated to do so. This is Interface
Stability — it means swapping hardware or backends never requires rebuilding Nova.

### File Locations
```
C:/Nova/
├── ingest.py               # Ingestion pipeline — reads Second Brain, writes to Chroma
├── nova_query.py           # RAG pipeline — retrieve + generate
├── nova_api.py             # FastAPI server — all external access goes through here
├── nova_router.py          # Query routing by category (lore, general, etc.)
├── graph_builder.py        # Builds nova_graph.json from Chroma wikilink metadata
├── nova_watcher.py         # Watchdog file monitor (deferred)
├── nova_logger.py          # Training data logger — detects character blending
├── nova_corrector.py       # DPO pair generator via Claude API
├── nova_chat.py            # CLI chat interface
├── nova_memory_store.py    # Conversation history persistence
├── nova_benchmark.py       # Performance benchmarking
├── nova_sources.py         # Source paths config — Second Brain location
├── nova_graph.json         # Wikilink graph — nodes + edges (output of graph_builder.py)
├── ingest_manifest.json    # Tracks file mtimes for incremental ingest
├── memory/                 # Chroma vector database (persistent)
└── logs/
    └── watcher.log         # File watcher logs
```

### Second Brain Location
```
C:/Users/marvi/OneDrive/Documents/Second Brain/
```
This is Marvin's Obsidian vault. It is the corpus Nova ingests. Never write to this directory.
Read-only always.

### Key External Dependencies
- **Ollama** — local LLM runner, model: `llama3.2` (LLaMA 3.2 3B)
- **Chroma** — local vector database at `C:/Nova/memory/`
- **Collection name** — `nova_memory`
- **Embedding function** — `DefaultEmbeddingFunction()` from `chromadb.utils`
- **Claude API** — used only by `nova_corrector.py` for DPO pair generation

### nova_graph.json Structure
```json
{
  "nodes": [{ "id": "Null.md", "title": "Null", "project": "Second Brain", "chunk_count": 5 }],
  "edges": [{ "source": "Null.md", "target": "Fatale Wildman.md", "link_text": "Fatale Wildman" }]
}
```
Validated output: 257 nodes, 301 edges. Hub nodes: KAS.md (49 connections),
Master Timeline.md (46), SYS_Symphony.EXE.md (25), Null.md (18).

---

## 3. Coding Philosophy — Legibility First

The single most important rule: **code must be easy to read, understand, troubleshoot,
and edit by a human at any time.**

Legibility takes priority over cleverness, brevity, and performance optimization unless
performance is a proven, measured problem.

### Core Rules

- **One job per function.** If a function does two things, split it.
- **Name things like sentences.** `retrieve_with_graph(query)` not `ret(q)`.
- **Plain English comment above every function.** Describe *what it does* and *why it exists*.
- **No magic numbers.** Every constant gets a named variable. `NUM_CTX = 8192` not `8192`.
- **No clever one-liners.** If it takes more than a moment to parse, rewrite it as steps.
- **Explicit over implicit.** Write what you mean.
- **Break complex logic into named steps.** Each step should be readable on its own.
- **Avoid deep nesting.** Use early returns and guard clauses to keep indentation flat.

### Comment Style
```python
def retrieve_with_graph(query: str, n_results: int = 3) -> list[dict]:
    """
    Graph-guided retrieval — calls get_context_budget() first to get a ranked
    list of relevant files from the wikilink graph, then scopes Chroma search
    to those files. Falls back to unfiltered search if context budget is empty.
    Used by ask() for all non-character-filtered queries.
    """
    ...
```

---

## 4. Python Style Guide

- Use `snake_case` for variables and functions.
- Use `PascalCase` for class names.
- Use `SCREAMING_SNAKE_CASE` for constants.
- Always declare types where practical: `def retrieve(query: str, n_results: int = 5) -> list[dict]:`
- Imports go at the top. Group: stdlib → third-party → local.
- Private/internal helpers are prefixed with `_underscore`.
- Group related functions with a `# ── Section Name ──` header comment (match existing style).

### File Structure Order
```
# 1. Module docstring / header comment
# 2. Imports
# 3. Constants / config
# 4. Setup (clients, collections, etc.)
# 5. Helper functions
# 6. Core functions
# 7. Main / entry point
```

---

## 5. Known Issues & Active Bugs

### /context-budget returns empty (ACTIVE BUG)
`GET /context-budget?query=X` returns `{"files": [], "count": 0}` for all queries.

**Confirmed working:** `/graph` and `/neighbors` both return correct data — graph is loaded.
**Isolated to:** `get_context_budget()` in `graph_builder.py` — the semantic search step
that finds seed nodes is returning zero results.

**Likely cause:** `get_context_budget()` is using a different Chroma client path, collection
name, or embedding function than `nova_query.py`. Compare `retrieve()` in `nova_query.py`
(which works) against `get_context_budget()` — they must use identical:
  - `chromadb.PersistentClient(path="C:/Nova/memory")`
  - collection name: `nova_memory`
  - `embedding_functions.DefaultEmbeddingFunction()`

**Expected result after fix:** `GET /context-budget?query=Tell me about Null` should return
a ranked list including `Null.md`, `Fatale Wildman.md`, `SYS_Symphony.EXE.md`, and connected
notes from BFS graph expansion.

---

## 6. RAG Architecture — Critical Details

### Character Query Handling
Fiction/lore queries with a named character use a direct `$eq` filter on `filename` metadata
in Chroma — bypassing graph-guided retrieval entirely. Known characters include:
`null`, `fatale`, `helel`, `raven`, `luci`, `marisol`, `beat`, `rhythm`, `varas`, `aseir`.

### Character Blending Fixes (already applied — do not revert)
1. System prompt contains source boundary rule — model told never to transfer attributes
   between sources
2. Every chunk is prefixed with `[filename]` before embedding — identity baked into vector
3. Fiction queries capped at 3 results (not 5 or 6)
4. History-contaminated retrieval fixed — character queries do not expand with prior turns
5. Per-character Chroma `$eq` filtering with unfiltered fallback

### Graph-Guided Retrieval Flow (retrieve_with_graph)
```
query → get_context_budget() → ranked filenames → Chroma $in filter → chunks
                                     ↓ if empty
                              unfiltered Chroma search → chunks
```

---

## 7. Nova API Routes

All routes live in `nova_api.py`. Run with:
```bash
nova-env\Scripts\python -m uvicorn nova_api:app --host 0.0.0.0 --port 8000
```

| Route | Method | Status | Description |
|---|---|---|---|
| /ask | POST | ✓ Working | Full RAG pipeline — query → retrieve → generate |
| /graph | GET | ✓ Working | Full node/edge map from nova_graph.json |
| /neighbors | GET | ✓ Working | Incoming + outgoing edges for a given file |
| /context-budget | GET | ✗ Bug | Should return ranked file list — returns empty |
| /ingest | POST | Untested | Trigger incremental ingest for a file |
| /rebuild-node | POST | Untested | Rebuild a single graph node |

---

## 8. What Claude Should Always Do

- Read and internalize this file before starting any task.
- Ask before creating new files outside the established structure.
- Use Plan Mode for any task that touches more than one file or system.
- Summarize what changed after completing a task — keep it short and clear:
  - What file(s) were changed
  - What was added or removed
  - Why
- Flag anything that might break an existing system before making the change.
- When editing an existing function, preserve its behavior unless explicitly told to change it.
- When responding to a change request, do three things together in this order:
  1. **Offer to make the change yourself** — don't just describe it and stop.
  2. **Show the specific line(s) the change affects** — with file path and actual lines.
  3. **Explain how and where you would refactor** before applying.
  The goal is to keep Marvin oriented and build troubleshooting intuition — never apply
  a change silently without surfacing what it touches and why.

---

## 9. What Claude Should Never Do

- Do not refactor code that wasn't part of the task.
- Do not rename things without asking — naming is intentional.
- Do not add dependencies without asking.
- Do not write to the Second Brain directory — read-only always.
- Do not talk directly to Chroma or Ollama from new code — route through FastAPI.
- Do not write TODO comments and leave them — either implement it or ask what to do.
- Do not optimize prematurely. Readable first, fast later if needed.
- Do not revert any of the character blending fixes in Section 6.

---

## 10. Change Log

| Date       | Change                                        | Reason                                      |
|------------|-----------------------------------------------|---------------------------------------------|
| 2026-06-15 | CLAUDE.md created                             | Establishing Nova project standards         |
| 2026-06-15 | Documented /context-budget bug in Section 5   | Active bug — first task for next session    |

---

## 11. Session Startup Checklist

At the start of every session, confirm:
1. You have read this file fully.
2. nova_api.py is running (`uvicorn nova_api:app --host 0.0.0.0 --port 8000`)
3. You know which task this session is focused on.
4. You are in Plan Mode if the task touches more than one file.

Then say: **"Ready. Working on [task]. Here's what I'm planning to do: [brief plan]."**

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
