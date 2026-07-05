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
- `nova_log.py` — Nova Log query telemetry (query_log.jsonl) + Health dashboard data
- `start_nova.ps1` / `launch_openwebui.ps1` — one-command local launch (nova_api.py + Open WebUI)

### Phase Roadmap
- Phase 0    | Foundation             | ✓ Complete
- Phase 1    | Memory Core            | ✓ Operational — Nova Log v1 done (Health view only, see Section 7); Nova Log Benchmark/Pipeline/Query views + log rotation deferred
- Phase 1.5  | Self-Monitoring        | Resource headroom calculator ✓ v1 live (2026-07-05 — `nova_headroom.py`, `GET /headroom`); Task Scheduler auto-start for nova_api.py/Open WebUI ✓ shipped (`nova_watcher.py` itself still not auto-started); periodic benchmarking suite ✓ v1 live (`nova_benchmark.py --golden`) — real Llama 3.2 3B baseline established in `logs/benchmark_log.jsonl`, doubling as the eval harness for Phase 3/3.5's model-swap criteria below
- Phase 1.75 | Retrieval Intelligence | Backlog — classical-algorithm pass over the retrieval/decision layer: A* graph traversal + document-level embeddings for the heuristic, DP context-window packing, priority-queue routing, two-tier memory decay, weighted wikilinks, link-aware ingestion upgrade, feature-flag system (nova_config.json)
- Phase 2    | Voice & Capture        | Backlog — Whisper + Piper
- Phase 2.5  | Agent Layer            | Backlog — file CRUD ✓ v1 live (`nova_tools.py`); MCP tool-calling + Nova MCP Server deferred (not on the critical path — nova_orchestrator.py calls nova_tools.py in-process); Docker sub-agent orchestration deferred, see Phase 3.5
- Phase 3    | First Fine-Tune        | Backlog — Unsloth + DPO → GGUF → Ollama (conversational/lore lane); base-model re-eval (Llama 3.2 3B vs. Phi-4 Mini 128K) + dynamic model routing. **Swap trigger, now measurable:** `nova_benchmark.py --golden` (Phase 1.5) established a real Llama 3.2 3B baseline in `logs/benchmark_log.jsonl` (8 golden queries across every router category — latency, routing accuracy, fiction blend rate); a candidate model must clearly beat that logged baseline, re-running the same script, before a swap — not a fixed timeline
- Phase 3.5  | Coding Agent Lane      | ✓ v1 live (2026-07-05) — Claude API-backed coding sub-agent (`nova_orchestrator.py`), git-worktree isolated, no Docker/OpenHands yet (deferred as a hardening pass); proven on 6 real merged tasks so far (headroom calculator, `start_nova.ps1` hardening, router integration + its own live test, Nova Log Query view, the golden benchmark suite itself). **Qwen3 8B swap trigger, not a fixed date:** (1) ~30-50 diverse real task transcripts accumulated in `logs/agent_log.jsonl` — already happening automatically every real `/agent/task` run, not a separate curation project; (2) ~20% held out as a never-trained-on eval set (same benchmarking suite as Phase 1.5/3); (3) swap only once Qwen3 clears a defined pass bar against that held-out set (completion rate within turn budget, no worse than Claude's baseline on the same tasks) — this is the path to Nova coding independently
- Phase 4    | Roaming Layer          | ✓ Lightweight v1 shipped (2026-07-05) — Tailscale installed + authenticated (this machine, "zeed", on the tailnet at `100.122.229.23`); Task Scheduler "Nova Auto-Start" runs `start_nova.ps1 -Silent` at login (idempotent, verified); sleep disabled on AC power only, battery behavior unchanged. Required two admin-elevated firewall rules (`Nova API (Tailscale)`, `Nova Open WebUI (Tailscale)` — ports 8000/3000, Private profile) since Tailscale's virtual adapter classifies as Private while the existing python.exe rules only covered Public/home-WiFi. Verified end-to-end from a phone reaching `http://100.122.229.23:3000` — that test was over the same home WiFi (Tailscale found a direct LAN path), so genuine away-from-home/cellular reachability hasn't been separately confirmed yet, though DERP relay fallback makes it likely to work. Heavier items (headless Ubuntu server, Dockerized services, cloud GPU, hosted inference fallback) remain deferred until there's a concrete forcing function — not a prerequisite for "always present"
- Phase 5    | Continuous Learning    | Backlog — quarterly fine-tune cycles
- Phase 6    | Domain Expansion       | Backlog — domain state layer + adapters (financial, alert engine), pixel RAG (CLIP/ColPali), chunk visualization tool, temporal awareness, proactive memory, content transformation pipeline, Art Practice Companion module

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
├── nova_log.py             # Nova Log — query telemetry writer + Health dashboard data
├── nova_log.html           # Nova Log Health dashboard — static page served at /nova-log
├── nova_sources.py         # Source paths config — Second Brain location
├── nova_tools.py           # Path-scoped file/exec primitives for the coding sub-agent
├── nova_orchestrator.py    # Coding sub-agent loop (Claude-backed v1, git-worktree isolated)
├── nova_graph.json         # Wikilink graph — nodes + edges (output of graph_builder.py)
├── ingest_manifest.json    # Tracks file mtimes for incremental ingest
├── start_nova.ps1          # Launches nova_api.py + Open WebUI, one command
├── launch_openwebui.ps1    # Open WebUI env vars + launch (called by start_nova.ps1)
├── memory/                 # Chroma vector database (persistent)
└── logs/
    ├── query_log.jsonl          # Per-query telemetry (nova_log.py) — Nova Log Health data source
    ├── agent_log.jsonl          # Per-turn coding sub-agent telemetry (nova_orchestrator.py)
    ├── agent_task_outcomes.jsonl # Merged/discarded label per branch (nova_orchestrator.record_task_outcome) — call by hand after each merge/discard decision; this is what turns agent_log.jsonl into a usable Qwen3 training set later
    └── watcher.log              # File watcher logs

C:/nova-agent-worktrees/    # Sibling dir, outside the repo — disposable per-task git
                            # worktrees created by nova_orchestrator.py. Outside both of
                            # ingest.py's configured sources (Second Brain + C:/Nova), so
                            # never ingested.
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
- **Claude API** — used by `nova_corrector.py` for DPO pair generation, and by
  `nova_orchestrator.py` as the interim coding sub-agent brain (see below)

### Nova Coding Sub-Agent (nova_orchestrator.py)
Nova can now write to its own codebase — the one sanctioned exception to a human
surfacing every change before it's applied (Section 8). Safety comes from **git worktree
isolation**, not manual review of each write: every task runs in its own disposable
worktree + branch under `C:/nova-agent-worktrees/`, never the live `C:/Nova` tree.
`nova_orchestrator.py` never merges or deletes a worktree — Marvin always reviews the
diff and merges by hand. v1 is driven by the Claude API (not a local model yet) and has
no Docker/OpenHands sandboxing — that's deferred; see Phase 3.5.

**Known limitation (accepted, 2026-07-05):** the worktree boundary is only hard-enforced
for `read_file`/`write_file`/`list_files` (path-validated against `root` in
`nova_tools.py`). `run_command` is a raw shell — it can `cd` outside the worktree and
reach the live tree, same trust level as Marvin's own shell. `nova_tools.py` has a
best-effort denylist for obviously destructive patterns (`rm -rf`, `git push`, `git reset
--hard`, etc.), but this is a speed bump, not real sandboxing. Confirmed live: a run
legitimately needed a Python interpreter (worktrees have no venv — `nova-env/` isn't
git-tracked) and ended up running `pip install` against the shared live venv rather than
something worktree-local. Accepted as reasonable for v1 — Claude is driving this, not an
adversarial actor, and every action is logged to `logs/agent_log.jsonl` — real containment
for `run_command` specifically remains deferred to the Docker/OpenHands hardening pass.

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

### /context-budget — FIXED (2026-06-17)
Previously returned `{"files": [], "count": 0}` for all queries. Root cause was a mismatch
between `get_context_budget()` and `nova_query.py`'s Chroma client path / collection name /
embedding function.

**Verified fixed on 2026-07-04:** `graph_builder.py` and `nova_query.py` now use identical
Chroma setup (`PersistentClient(path="C:/Nova/memory")`, collection `nova_memory`,
`DefaultEmbeddingFunction()`). Calling `get_context_budget("Tell me about Null")` directly
returns a 15-file ranked list including `Null.md`, `Nullius.md`, `Fatale Wildman.md`, and
`SYS_Symphony.EXE.md` — matching the expected post-fix behavior.

No known active bugs at this time.

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
| /context-budget | GET | ✓ Working | Returns ranked file list from graph-guided seed search |
| /ingest | POST | Untested | Trigger incremental ingest for a file |
| /rebuild-node | POST | Untested | Rebuild a single graph node |
| /v1/models | GET | ✓ Working | OpenAI-compatible model list (Open WebUI model picker) |
| /v1/chat/completions | POST | ✓ Working | OpenAI-compatible chat — routes Open WebUI through the RAG pipeline |
| /nova-log | GET | ✓ Working | Nova Log Health dashboard (HTML) |
| /nova-log/data | GET | ✓ Working | Nova Log Health dashboard data (JSON) — real stats only, see Section 1 |

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
| 2026-07-04 | Marked /context-budget as fixed in Sections 5 & 7 | Verified via direct call — bug doc was stale after the 2026-06-17 fix |
| 2026-07-04 | Added Open WebUI OpenAI-compat routes and launch scripts to Sections 1, 2 & 7 | Doc catch-up — these shipped same day but weren't recorded in CLAUDE.md |
| 2026-07-04 | Added Nova Log v1 (query_log.jsonl + /nova-log Health dashboard) to Sections 1, 2 & 7 | Only the Health view is buildable against real data today — Benchmark/Pipeline/Query views and their log files depend on nova_orchestrator.py and a real benchmark delta-log, neither of which exist yet |
| 2026-07-05 | Reconciled Phase Roadmap (Section 1) with ~40 new ClickUp backlog tasks — added Phase 1.75 Retrieval Intelligence, Phase 3.5 Coding Agent Lane, and Phase 6 Domain Expansion; expanded 1.5/2.5/3/4 descriptions | ClickUp board grew significantly past what CLAUDE.md documented; Phase 3.5 specifically splits out a dedicated OpenHands + Qwen3 8B coding sub-agent lane, run parallel to Phase 3's conversational fine-tune, as an architectural shortcut toward Nova coding independently |
| 2026-07-05 | Shipped Phase 3.5 v1 (`nova_tools.py`, `nova_orchestrator.py`, `POST /agent/task`) — Claude API-backed coding sub-agent, git-worktree isolated instead of Docker/OpenHands | Docker isn't installed on this machine and Qwen3 8B has no training data yet — waiting for both would block "Nova coding itself" indefinitely. Building the harness now with Claude as an interim brain, then swapping to Qwen3 once trained, makes the eventual fine-tune cheaper (real usage becomes training data) instead of leaving the scaffolding idle. Docker/OpenHands remains a deferred hardening pass |
| 2026-07-05 | Merged first real coding sub-agent task — resource headroom calculator (`nova_headroom.py`, `GET /headroom`, commit `c516cca`); fixed 3 real bugs it surfaced (Windows shell mismatch, no venv inside worktrees, orchestrator never committing its own work) in `16a6f20` | Proved the harness end-to-end rather than just via a smoke test — exactly the "prove it with a reliable brain first" reasoning behind Phase 3.5's sequencing decision |
| 2026-07-05 | Defined concrete swap triggers for Phase 3 (base model) and Phase 3.5 (Qwen3 8B), and re-scoped Phase 4 to a lightweight Tailscale + auto-start v1 ahead of the heavier headless-server/Docker items | Marvin asked for an actual end-goal rather than an open-ended "eventually" — a benchmark-suite-gated pass bar (not a fixed date or task count) applies to both model swaps; the lightweight roaming approach gets "Nova on any device" now, using existing hardware, without waiting on Phase 4's heavier backlog items which solve a different problem (freeing the daily-driver machine) |
| 2026-07-05 | Shipped Phase 4 lightweight v1 — Tailscale, Task Scheduler auto-start, AC-only no-sleep, two new firewall rules; also gave `nova_orchestrator.py` its second real task (`start_nova.ps1`/`launch_openwebui.ps1` hardened for idempotent/silent auto-start, commits `07365bd`/`662ab49`) | Verified end-to-end from a phone reaching Open WebUI over Tailscale. Found along the way: Tailscale's adapter classifies as Private while existing python.exe firewall rules only covered Public, so remote access needed new admin-elevated rules; iOS requires the VPN toggle explicitly on, not just being signed in, to actually establish the tunnel (confirmed via `tailscale ping` failing, then succeeding once the toggle was on) |
| 2026-07-05 | Added `record_task_outcome()` (`logs/agent_task_outcomes.jsonl`, commit `9651608`), router integration (`/code ` prefix in chat routes to the coding sub-agent, commit `2ad8960`), Nova Log Query view (commit `201c0a9`), and the golden-query RAG benchmark suite establishing a real Llama 3.2 3B baseline (commit `9baf363`, encoding-crash fix in `49397f9`) | Continuing to feed the coding sub-agent real backlog tasks — 4 more merged (6 total). The benchmark suite specifically makes Phase 3's swap trigger concrete rather than aspirational: an actual logged baseline now exists in `benchmark_log.jsonl` for a candidate model to beat. Found along the way: nova_benchmark.py's existing ✓/✗ output was a pre-existing crash on a plain Windows console (cp1252), only surfaced once the golden benchmark actually got run for real to verify it |

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
