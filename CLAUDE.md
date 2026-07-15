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
- `nova_config.py` / `nova_config.json` — feature-flag system gating future classical-algorithm
  augments and framework integrations (all off today); `config_snapshot()` is attached to every
  query's telemetry in `query_log.jsonl` so flag state is tied to results before any augment exists
- `nova_mcp_server.py` — standalone MCP server exposing `nova_api.py`'s routes as MCP tools
  (`nova_query`, `nova_graph`, `nova_neighbors`, `nova_context_budget`, `nova_ingest`) over
  streamable-http on port 8100; thin `httpx` client over the FastAPI routes, no direct Chroma/Ollama
  access. Not yet wired into anything — nothing in Nova's own codebase calls it, and no MCP client
  has been pointed at it
- `nova_chroma_omen_check.py` — 4-step Chroma-on-Omen reachability probe (raw TCP → HttpClient
  heartbeat → `nova_memory` collection lookup → a real query), `--host`/`--port` overridable;
  distinguishes "infra not up yet" from a real code bug before anything gets wired to it
- `nova_board.py` / `nova_clickup_client.py` — terminal CLI enforcing the ClickUp board's
  dependency/status house rules (`ready`/`why`/`check`/`audit`/`move`/`block`/`link`/`split`);
  a cheaper path to board maintenance than round-tripping the ClickUp MCP tools for every action
- `nova_status_digest.py` — snapshots the board's ready/in-progress/blocked state to
  `NOVA_STATUS.md`, diffed against the previous run via `.nova_status_snapshot.json`; one-way
  (Claude Code writes it after sessions that change board state, Claude Chat reads it as a cheap
  starting point)
- `nova_chunk_viz.py` — RAG retrieval-audit CLI (`86bara3tj`, stage 1 of 3: CLI → simple web
  view → Open WebUI debug panel, only CLI built so far). Given a query, prints which chunks
  Chroma actually retrieved (source, chunk index, distance, character tag), mirroring
  `nova_query.ask()`'s exact retrieval branching so it reflects real production behavior, not
  a simplified demo
- `nova_embedding_viz.py` / `nova_embedding_viz.html` — Embedding-Space Visualization
  (`86bawjg14`). Projects every Chroma chunk to 2D (`sklearn.manifold.TSNE`, no new pip
  dependency) so Marvin can visually audit character cluster overlap, with retrieval-hit and
  DPO-correction overlays. Distinct from `nova_chunk_viz.py` — that debugs one query's
  results, this audits the whole corpus's cluster structure at once. Served at
  `GET /embedding-viz` (page) / `GET /embedding-viz/data` (JSON), matching `/nova-log`'s
  pattern exactly
- `nova_usage_logger.py` — usage-history-baseline component of `86bawx7vj` (headless Nova
  coding runner). Scans every local Claude Code session transcript
  (`~/.claude/projects/**/*.jsonl`, all projects — usage draws from one account-wide
  subscription pool, not per-project) and aggregates real token usage + estimated cost by
  calendar day into `logs/claude_usage_history.json` (fully regenerated each run, same
  convention as `nova_status_digest.py` — not an append log). Exists because `/cost`
  doesn't work through headless `-p` mode (confirmed live — it gets sent as literal text to
  the model instead of being intercepted as a UI command); local transcripts already carry
  the same per-message `usage` data `/cost` would have shown interactively. No live
  quota-forecast API exists for Claude Code, so this is the self-tracked substitute
  `86bawx7vj` calls for. **Centralization (2026-07-14):** a `SessionEnd` hook
  (`.claude/settings.json`, all termination reasons) runs `--push` on every session
  end, sending this machine's aggregate to `nova_api.py`'s new `POST /usage-history`,
  which merges it into `nova_state.db`'s `system/claude_usage_history` entity — a
  deliberate extension beyond Architecture Principles v1.1's original Principle 6
  list. Push target defaults to `localhost:8000` (verified end-to-end there);
  pointing it at the Omen is a separate commit+deploy step, not done yet
- `nova_tool_call_log.py` — tool-call logging schema for the coding sub-agent
  (`86bawntpb`). One JSONL entry per tool call (`logs/tool_call_log.jsonl`) —
  `tool_call_id`, `agent`, `session_id`, `tool`, `args`, `result`
  (success/error — `timeout` isn't distinguishable yet, `run_command` has no
  separate timeout signal), `latency_ms`, plus `was_necessary`/`was_used`
  fields that start `null` and get filled by a future async judge-pass or
  manual flag (not built — see `86bawntpm`). Wired into
  `nova_orchestrator.py`'s `_execute_tool` (logs every call regardless of
  caller; `session_id` is optional so the LangGraph path, which doesn't pass
  one yet, degrades safely rather than breaking). **Deliberately interim**:
  `86bax697m` (Langfuse, confirmed as the definite direction) is expected to
  absorb this as trace/observation instrumentation later — built now anyway
  per Marvin's explicit sequencing call, accepted as throwaway once Langfuse
  actually lands
- `nova_omen_dispatch.py` — headless task dispatch on the Omen, the real
  "invocation" step of `86bax0exx`'s orchestration layer. Wraps
  `claude -p --worktree` over SSH (Tailscale IP, works whether or not the
  Aero is on the same LAN) — worktree isolation uses Claude Code's own
  native `--worktree` flag, not `nova_orchestrator.py`'s hand-rolled
  version. Proven live 2026-07-14: `dispatch_headless_task()` returns a
  clean structured result (session_id, summary, cost, stop_reason).
  **Bounding mechanism, honestly stated**: this CLI version has no
  `--max-turns` flag (checked directly) — a wall-clock subprocess timeout
  (30 min) is the real safety backstop, not a turn count, contrary to what
  `86bawx7vj`'s original spec assumed was available. Invocation primitive
  only — no task-queue polling, no escalation handling (`86bax0wkj`, not
  built), no pause-at-will (not built). Never merges/deletes its own
  worktrees, matching `nova_orchestrator.py`'s safety model exactly

### Phase Roadmap
- Phase 0    | Foundation             | ✓ Complete
- Phase 1    | Memory Core            | ✓ Operational — Nova Log: Health, Query, and Benchmark views all live (see Section 7); only Pipeline view remains, genuinely blocked on the content pipeline (Phase 6, `86bage4ff`); log rotation still deferred
- Phase 1.5  | Self-Monitoring        | Resource headroom calculator ✓ v1 live (2026-07-05 — `nova_headroom.py`, `GET /headroom`); Task Scheduler auto-start for nova_api.py/Open WebUI ✓ shipped (`nova_watcher.py` itself still not auto-started); periodic benchmarking suite ✓ v1 live (`nova_benchmark.py --golden`) — real Llama 3.2 3B baseline established in `logs/benchmark_log.jsonl`, doubling as the eval harness for Phase 3/3.5's model-swap criteria below
- Phase 1.75 | Retrieval Intelligence | Backlog, build-first foundation laid — feature-flag system ✓ v1 live (2026-07-06, `nova_config.py`/`nova_config.json`, all flags off; `config_snapshot()` wired into every query's `query_log.jsonl` entry so flag state is tied to results before any augment exists). Remaining: A* graph traversal + document-level embeddings for the heuristic, DP context-window packing, priority-queue routing, two-tier memory decay, weighted wikilinks, link-aware ingestion upgrade — each will gate on a flag already defined above
- Phase 2    | Voice & Capture        | **Explicitly greenlit early, in progress (`86baeyg3q`, confirmed with Marvin 2026-07-12)** — Whisper Distil (STT) + Piper (TTS) + "Hey Nova" wake word + iPhone/Apple Watch quick-capture, target 2-4s full round-trip, explicit-consent-only (no passive public capture). This is a deliberate exception to the "don't build Phase 2+ without explicit instruction" rule below, not a stale status — confirm with Marvin if picking this back up after a gap to make sure the exception still stands
- Phase 2.5  | Agent Layer            | Backlog — file CRUD ✓ v1 live (`nova_tools.py`); Nova MCP Server ✓ v1 live (2026-07-06, `nova_mcp_server.py`, wraps `/ask` `/graph` `/neighbors` `/context-budget` `/ingest` as MCP tools over streamable-http:8100) but not yet wired to any MCP client or into `nova_orchestrator.py` itself — `mcp`/`httpx` are installed in `nova-env` but this is an unused standalone server until something connects to it; Docker sub-agent orchestration deferred, see Phase 3.5. **Browser Hands harness (M1) ✓ v1 live (2026-07-10, ClickUp `86barqzmv`):** `browser_hands/` package (`harness/cdp_connect.py`, `retry.py`, `selector_discovery.py`, `tree_walk.py`, `state_writer.py`, `config/sites.yaml`) — CDP-attach-only browser automation foundation, generalized from the proven `C:\Projects\developer_tools\base44_export.py` reference script. No automated login, ever (hard rule). Adapters (M2 PiSignage health-check `86barqztk`, M3 website audit `86barqzy8`, M4 subscription audit `86barr02x`, M5 Base44 conform `86barr06e`) are separate, still-backlog tasks — `browser_hands/adapters/` stays empty until then. New `playwright` dependency (pip package only, no browser-binary download needed since it only ever attaches to Marvin's own already-running Chrome). Writes to a new `browser_tasks` table in `nova_state.db`, separate from the generic `domain_state` table (an event log, not a state snapshot)
- Phase 3    | First Fine-Tune        | Backlog — Unsloth + DPO → GGUF → Ollama (conversational/lore lane); base-model re-eval (Llama 3.2 3B vs. Phi-4 Mini 128K). **Swap trigger, now measurable:** `nova_benchmark.py --golden` (Phase 1.5) established a real Llama 3.2 3B baseline in `logs/benchmark_log.jsonl` (8 golden queries across every router category — latency, routing accuracy, fiction blend rate); a candidate model must clearly beat that logged baseline, re-running the same script, before a swap — not a fixed timeline. **Model-swap eval wrapper ✓ v1 live (2026-07-10, `nova_benchmark.py --evaluate <model>`):** one command pulls a candidate, runs the golden benchmark on it for real (fixed a real bug first — `run_golden_benchmark`'s `model_label` used to only change the log tag, never the actual model used for generation), and prints a pass/fail verdict against the logged baseline with per-metric deltas. **Dynamic model routing ✓ mechanism live (2026-07-10, `nova_config.json`'s `model_routing`, default off):** `nova_query.ask()` resolves a per-category model via `get_routed_model()` instead of a single hardcoded constant — every category maps to `llama3.2` today (the only model actually adopted), so behavior is unchanged by default; swapping in Phi-4 Mini/Qwen3 8B once either is actually adopted (still blocked on `86bagf51n`/`86bagek35`/`86bara7zk`, all backlog) is a config edit, not a code change. Verified live by temporarily routing one category to `llama3.1:8b` (the other model already pulled locally) and confirming the logged model field showed the swap really happened. **Dual-model VRAM fit — NO-GO, confirmed empirically 2026-07-11 (`86bagek35`):** `qwen3:8b` alone uses 86% of the Aero's 8GB card (real `nvidia-smi` measurement — Ollama's own `ollama ps` "SIZE" column undercounts by ~1.5GB, CUDA context/KV cache overhead isn't included there); loading `phi4-mini` alongside it evicts it every time, confirmed both directions, regardless of `OLLAMA_MAX_LOADED_MODELS`. The routing *mechanism* above is unaffected (pure config lookup, no model management), but in practice a category switch that lands on a cold model means a real load/unload latency hit, not instant switching — factor this into how aggressively categories get split across models vs. kept on one default once dual-model routing is revisited. Also surfaced: Qwen3 ships with "thinking mode" on by default, likely inflating the original `86bauwqqd` benchmark's 15728ms average — re-benchmark with `think=False` before drawing conclusions on Qwen3 8B's real fitness. Still open on `86bagek35`: context-fill latency at 8K/32K/64K/128K, quality comparison, cold-start latency, VRAM headroom with Chroma actually running (all prior tests had Chroma off — real headroom is lower now that it's live on the Omen)
- Phase 3.5  | Coding Agent Lane      | ✓ v1 live (2026-07-05) — Claude API-backed coding sub-agent (`nova_orchestrator.py`), git-worktree isolated, no Docker/OpenHands yet (deferred as a hardening pass); proven on 6 real merged tasks so far (headroom calculator, `start_nova.ps1` hardening, router integration + its own live test, Nova Log Query view, the golden benchmark suite itself). **Qwen3 8B swap trigger, not a fixed date:** (1) ~30-50 diverse real task transcripts accumulated in `logs/agent_log.jsonl` — already happening automatically every real `/agent/task` run, not a separate curation project; (2) ~20% held out as a never-trained-on eval set (same benchmarking suite as Phase 1.5/3); (3) swap only once Qwen3 clears a defined pass bar against that held-out set (completion rate within turn budget, no worse than Claude's baseline on the same tasks) — this is the path to Nova coding independently. **LangGraph orchestration v1 live (2026-07-10, ClickUp `86bat0u81`):** `nova_orchestrator.py`'s per-task turn loop can now run through `nova_orchestrator_graph.py` (new file) instead of the original inline loop — LangGraph owns graph state, node transitions, and checkpointing; Nova keeps the task registry schema, Docker container lifecycle, and ClickUp/skill injection. **Scope note:** this ports the loop that actually exists today (`run_coding_task()` runs exactly one task, no parallelism) — the ticket's own language about "sequential + parallel patterns" assumed multi-task orchestration that was never built; that's Section 17's future sub-agent-orchestration vision, not this task. Gated behind `framework_integrations.langgraph_orchestration` in `nova_config.json` (default off, original inline loop untouched when off — including at import time, since `nova_orchestrator_graph` is only imported inside the flag branch). Verified: the same trivial task run through both paths produced identical `final_status`, turn counts, and `agent_log.jsonl` shape. Coordinated with the still-backlogged OpenHands integration (`86barex1u`), which plugs into this same layer as the coding-lane sub-agent
- Phase 4    | Roaming Layer          | ✓ Lightweight v1 shipped (2026-07-05) — Tailscale installed + authenticated (this machine, "zeed", on the tailnet at `100.122.229.23`); Task Scheduler "Nova Auto-Start" runs `start_nova.ps1 -Silent` at login (idempotent, verified); sleep disabled on AC power only, battery behavior unchanged. Required two admin-elevated firewall rules (`Nova API (Tailscale)`, `Nova Open WebUI (Tailscale)` — ports 8000/3000, Private profile) since Tailscale's virtual adapter classifies as Private while the existing python.exe rules only covered Public/home-WiFi. Verified end-to-end from a phone reaching `http://100.122.229.23:3000` — that test was over the same home WiFi (Tailscale found a direct LAN path), so genuine away-from-home/cellular reachability hasn't been separately confirmed yet, though DERP relay fallback makes it likely to work. **HP Omen headless Ubuntu server (`86baeyfm1`) — ✓ COMPLETE, verified 2026-07-12 (see Section 2):** all 13 phases (0-12) of `omen_setup_runbook.md` done and confirmed live, not just reported. OS/static-IP/packages/Chroma-data-transfer/lid-close/SSH/systemd/firewall/Tailscale all done. Chroma migrated `PersistentClient` → `HttpClient`, hosted on the Omen at `192.168.1.250:8000` (also reachable at its Tailscale IP `100.114.197.117:8000`); `nova-api` runs there on **8001**, not 8000 (real port conflict with Chroma, both defaulted there), reachable on both IPs too. Aero-side/Omen-side Ollama-callback groundwork (`OLLAMA_HOST=0.0.0.0` + `Nova Ollama (Omen callback)` firewall rule, TCP 11434) confirmed working in both directions — including a `curl` run from the Omen's own shell reaching the Aero's Ollama over the tailnet. Inference while Nova runs standalone on the Omen is split three ways: the Aero's own Ollama as primary (above, now live), hosted chat-API fallback (`86baf4eah` — Groq/Together.ai/Fireworks, non-sensitive queries only), serverless/raw GPU rental to run Nova's own weights (`86baw3010` — RunPod/Modal/Vast.ai-style), and a dedicated GPU machine purchase from a third-party vendor for local heavy inference (`86baw3016`). **`nova_api.py` deployed to the Omen, independent of the Aero (`86bawfn19`) — ✓ COMPLETE, verified 2026-07-12:** `nova-api` runs as a systemd unit on the Omen (port 8001, enabled, survives reboots), reachable on both LAN and Tailscale IPs; Ollama callback to the Aero confirmed working both directions; **real test of done passed — reached the Omen's `nova-api` over Tailscale with the Aero fully powered off and got a real grounded answer back.** This closes the last real gap in "Nova reachable from my phone independent of the Aero being on." Caveat: no Open WebUI on the Omen yet, so this is raw-API reachability, not a chat UI — Open WebUI still only runs on the Aero. **Now unblocked, next candidate to pick up:** Dockerized services (`86baf4e29`), held deliberately until this task finished. New ClickUp task `86bawf2z2` (token-based auth for `nova_api.py`) filed as separate, deliberately deferred scope — network-level controls (Tailscale/ufw) come first
- Phase 5    | Continuous Learning    | Backlog — quarterly fine-tune cycles
- Phase 6    | Domain Expansion       | Backlog, domain state layer foundation laid — `nova_state.db` schema + system adapter ✓ v1 live (2026-07-07, see Section 2); financial/work/creative/games adapters and the alert engine remain blocked on real open questions (data source approval, ClickUp access from Nova's own runtime). **Chunk visualization tool (`86bara3tj`) — CLI stage ✓ v1 live (2026-07-12, `nova_chunk_viz.py`)**, web view + Open WebUI panel stages still backlog. **Embedding-space visualization (`86bawjg14`) ✓ v1 live (2026-07-12, `nova_embedding_viz.py`/`.html`)** — t-SNE projection of the full corpus, character cluster overlap + retrieval-hit + DPO-correction overlays, `GET /embedding-viz`. Also backlog: pixel RAG (CLIP/ColPali), temporal awareness, proactive memory, content transformation pipeline, Art Practice Companion module

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
├── nova_query.py           # RAG pipeline — retrieve + generate (model_override + per-category routing via nova_config.get_routed_model)
├── nova_api.py             # FastAPI server — all external access goes through here
├── nova_router.py          # Query routing by category (lore, general, etc.)
├── graph_builder.py        # Builds nova_graph.json from Chroma wikilink metadata
├── nova_watcher.py         # Watchdog file monitor (deferred)
├── nova_logger.py          # Training data logger — detects character blending
├── nova_corrector.py       # DPO pair generator via Claude API
├── nova_chat.py            # CLI chat interface
├── nova_memory_store.py    # Conversation history persistence
├── nova_benchmark.py       # Performance benchmarking (--golden, --evaluate <model> for one-command model-swap checks)
├── nova_log.py             # Nova Log — query telemetry writer + Health dashboard data
├── nova_log.html           # Nova Log Health dashboard — static page served at /nova-log
├── nova_sources.py         # Source paths config — Second Brain location
├── nova_tools.py           # Path-scoped file/exec primitives for the coding sub-agent
├── nova_orchestrator.py    # Coding sub-agent loop (Claude-backed v1, git-worktree isolated)
├── nova_orchestrator_graph.py # LangGraph port of the turn loop (langgraph_orchestration flag, default off)
├── nova_config.py          # Feature-flag reads (is_augment_enabled, config_snapshot, etc.)
├── nova_config.json        # Feature-flag values — all off today (Phase 1.75 gating)
├── nova_mcp_server.py      # Standalone MCP server wrapping nova_api.py routes (unwired, port 8100)
├── nova_chroma_omen_check.py # Chroma-on-Omen reachability probe (TCP → heartbeat → collection → real query)
├── nova_usage_logger.py    # Local Claude Code usage/cost history (scans ~/.claude/projects/**/*.jsonl, all projects)
├── nova_tool_call_log.py   # Tool-call logging schema for the coding sub-agent (interim — Langfuse will absorb this)
├── nova_omen_dispatch.py   # Headless task dispatch on the Omen via `claude -p --worktree` over SSH (86bax0exx's invocation step)
├── nova_board.py           # Terminal CLI for ClickUp board dependency/status maintenance
├── nova_clickup_client.py  # ClickUp API client used by nova_board.py and nova_status_digest.py
├── nova_status_digest.py   # Writes NOVA_STATUS.md — board state snapshot, diffed run to run
├── NOVA_STATUS.md          # Output of nova_status_digest.py — ready/in-progress/blocked digest
├── .nova_status_snapshot.json # Previous digest run, tracked in-repo (diff source for the next one)
├── browser_hands/          # Browser automation harness (M1 only — see Phase 2.5). First nested package in the repo.
│   ├── harness/            # cdp_connect.py, retry.py, selector_discovery.py, tree_walk.py, state_writer.py
│   ├── adapters/           # Empty — M2-M5 (PiSignage, website audit, subscription audit, Base44) not built yet
│   └── config/             # sites.yaml (template only) + config.py loader
├── nova_graph.json         # Wikilink graph — nodes + edges (output of graph_builder.py)
├── ingest_manifest.json    # Tracks file mtimes for incremental ingest
├── start_nova.ps1          # Launches nova_api.py + Open WebUI, one command
├── launch_openwebui.ps1    # Open WebUI env vars + launch (called by start_nova.ps1)
├── omen_setup_runbook.md   # HP Omen headless Ubuntu server setup — concrete step-by-step commands
├── memory/                 # Legacy local Chroma PersistentClient data — superseded by the Omen-hosted
│                           # HttpClient server (see Key External Dependencies below), kept as-is, not deleted
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
- **Ollama** — local LLM runner on the Aero, model: `llama3.2` (LLaMA 3.2 3B). As of
  2026-07-12, also reachable over Tailscale (`OLLAMA_HOST=0.0.0.0` + a `Nova Ollama (Omen
  callback)` firewall rule, TCP 11434, Private profile) so the Omen — once on the tailnet —
  can call back to the Aero's Ollama instead of running inference itself
- **Chroma** — **as of 2026-07-11, Omen-hosted via `chromadb.HttpClient(host="192.168.1.250",
  port=8000)`**, not the local `PersistentClient` this file originally documented. All three
  call sites (`ingest.py`, `graph_builder.py`, `nova_query.py`) use identical
  host/port/collection/embedding-function config — same discipline as the original
  `/context-budget` fix (Section 5). `C:/Nova/memory/` still exists on disk but is legacy —
  no script reads it anymore. See "HP Omen Headless Server" below
- **Collection name** — `nova_memory`
- **Embedding function** — `DefaultEmbeddingFunction()` from `chromadb.utils`
- **Claude API** — used by `nova_corrector.py` for DPO pair generation, and by
  `nova_orchestrator.py` as the interim coding sub-agent brain (see below)

### HP Omen Headless Server (ClickUp `86baeyfm1`) — ✓ COMPLETE (2026-07-12)
Repurposed the HP Omen as an always-on Ubuntu service host for Chroma, `nova_state.db`, and
orchestration — replacing the Aero (which sleeps) for those specifically. **Confirmed service-
host-only, not a model-inference host**: its GTX 1050 Ti (4GB, Pascal) can't run the planned
dual-model routing. Full step-by-step commands live in `omen_setup_runbook.md` (all 13 phases,
0-12, marked done).

**Live and verified end-to-end, not just reported:** Ubuntu 24.04 installed (static IP
`192.168.1.250` on `eno1`), Chroma migrated from `PersistentClient` to `HttpClient` and running
as a standalone server there (port 8000), Chroma data transferred from the Aero, lid-close set
to ignore (SSH-able while closed), SSH from the Aero via key-based auth, `nova-chroma` and
`nova-api` running as permanent systemd units, `ufw` tightened to LAN-subnet-only, **Tailscale
live on the Omen (tailnet IP `100.114.197.117`, hostname `nova`)**. **`nova-api` runs on port
8001 on the Omen** — not the 8000 this file documents elsewhere for local Aero dev — because
both services defaulted to 8000 and conflicted once co-located on the same box.

**Final validation, all confirmed live:** `nova_chroma_omen_check.py` full pass against both the
LAN IP and the Tailscale IP (heartbeat OK, `nova_memory` collection found at 479 chunks, a real
query returned real results); `nova_api.py` returns `200` on `/`, `/headroom`, `/docs` on both
IPs (`/graph` correctly 404s — `nova_graph.json` hasn't been generated on this clone yet,
unrelated to setup); and the Ollama callback path — Aero's `OLLAMA_HOST=0.0.0.0` + a `Nova
Ollama (Omen callback)` firewall rule (TCP 11434, Private profile) — confirmed working in both
directions, including a `curl http://100.122.229.23:11434/` run **from the Omen's own shell**
returning `Ollama is running`.

**Now unblocked:** Dockerizing these services (`86baf4e29`) was deliberately held until this
task finished — it's the next real candidate to pick up. Re-running the Tailscale DERP relay
reachability test (`86bat0ue1`) against the Omen instead of the Aero is also now possible, not
done yet.

**Real bugs found and fixed along the way, not just workbook steps:** a second physical disk
still had the old Windows install — wiped and reclaimed; a nested-folder flatten, a UTF-16-
encoded `requirements.txt` (artifact of PowerShell's default output encoding), and `pywin32`
(Windows-only) needed stripping from the cloned repo/venv on Ubuntu; the Chroma/`nova-api` port
conflict above; `nova_orchestrator.py`'s `load_dotenv(dotenv_path="C:/Nova/.env")` was hardcoded
to a Windows path — broke silently on Linux (returns `False`, not a raised error, so the real
failure only surfaced later as a confusing "env var not set") — fixed to resolve relative to the
script's own location instead.

**Board hygiene note:** the runbook itself claimed SSH access into the Omen "closes out ClickUp
`86bavtz06`" — checked the actual task and it's really "Onboard Nova server, Pi fleet, and
trading bot box via SSH," three targets, only one done. Moved to "in progress," not "complete."
Same class of false-completeness as the OpenHands/RAGAS cases the ClickUp workflow-split memory
already flagged — always open the real task before trusting a prose completion claim.

### Important gap in the "COMPLETE" verification above, found and fixed same day (`86bawfn19`)
The verification that marked `86baeyfm1` complete checked **reachability** (`200` on `/`,
`/headroom`, `/docs`) but never actually exercised `/ask`'s real RAG behavior on the Omen's own
deployment. Picking up `86bawfn19` (deploy `nova_api.py` to the Omen, independent of the Aero)
surfaced a real, serious gap that reachability checks alone had missed:

1. **The Omen's git clone was 15 commits stale** — still on `5146222`, missing `b5f7f68` (the
   actual `PersistentClient` → `HttpClient` migration) and everything after it. `nova_query.py`
   there was still the old code, silently defaulting to an empty local Chroma store instead of
   the real `nova-chroma` server — `collection.count()` was `0`, and `/ask` returned fluent but
   **completely hallucinated** answers (e.g. claimed Null "was the lead singer of a fictional
   industrial rock band called Riven" — not in the corpus at all) with empty `sources`/`chunks`,
   no error, no signal anything was wrong except the content being wrong.
2. **Root cause of the stale clone: the GitHub deploy key (`id_ed25519_github`) was
   passphrase-protected**, which silently breaks any unattended `git pull`/`fetch` — there's no
   TTY to prompt for it. `ssh -T git@github.com` returned a flat `Permission denied (publickey)`
   even after re-pasting the (correct) public key to GitHub, because the private key could never
   actually be used to authenticate in the first place. Fixed by regenerating the key with no
   passphrase (`ssh-keygen -N ''`) — the whole point of a deploy key is unattended access, so a
   passphrase defeats its purpose; already locked to 600 perms and scoped to one repo.
3. Once fetchable, `git pull` brought the Omen fully current (`5146222` → `01b0866`, 14 files).
   `requirements.txt` needed `pywin32` filtered out before `pip install` (Windows-only, same
   gotcha the runbook already documented) — piped through `grep -v` rather than editing the
   tracked file, to avoid recreating the exact "local diff drifts from origin" problem that
   caused the stale-requirements.txt half of this mess in the first place.
4. **A second hardcoded-Windows-path bug, same class as the dotenv one already fixed**:
   `nova_api.py`'s `GRAPH_PATH = "C:/Nova/nova_graph.json"` silently returned empty nodes/edges
   on Linux instead of erroring. Fixed to resolve relative to the script's own location
   (`nova_api.py:58`).

**Lesson:** "reachable" and "functionally correct" are different claims — a route returning `200`
doesn't mean it's doing real work. Verify the payload, not just the status code, especially for
routes that can fail open (wrong data, not an error) rather than fail loud.

**`86bawfn19` — COMPLETE.** All 4 scope items done: requirements.txt encoding (resolved as part
of the stale-clone fix above — origin's copy was already correct UTF-8, the Omen's local UTF-16
copy was a stale artifact discarded via `git checkout -- requirements.txt`), `nova-api` running
as a systemd unit on the Omen (port 8001, enabled, survives reboots), the Ollama-callback path
(Aero `OLLAMA_HOST=0.0.0.0` + firewall rule, confirmed both directions), and the real test of
done — reaching the Omen's `nova-api` over Tailscale with the Aero fully powered off, getting a
real grounded answer back. This closes the last real gap in "Nova reachable from my phone
independent of the Aero being on" (note: no Open WebUI on the Omen yet, so this is the raw API,
not a chat UI — Open WebUI still only runs on the Aero).

### Nova Coding Sub-Agent (nova_orchestrator.py)
Nova can now write to its own codebase — the one sanctioned exception to a human
surfacing every change before it's applied (Section 8). Safety comes from **git worktree
isolation**, not manual review of each write: every task runs in its own disposable
worktree + branch under `C:/nova-agent-worktrees/`, never the live `C:/Nova` tree.
`nova_orchestrator.py` never merges or deletes a worktree — Marvin always reviews the
diff and merges by hand. v1 is driven by the Claude API (not a local model yet) and has
no Docker/OpenHands sandboxing — that's deferred; see Phase 3.5.

**LangGraph orchestration (2026-07-10):** the turn loop inside `nova_orchestrator.py`
can now run via `nova_orchestrator_graph.py` (LangGraph nodes/edges) instead of the
original inline loop, gated behind `framework_integrations.langgraph_orchestration`
(default off) — see Phase 3.5 and ClickUp `86bat0u81`.

**Tool preference (2026-07-07):** `file_replace(path, old_str, new_str)` — a
search/replace primitive in `nova_tools.py` — is preferred over `write_file`
for edits to files that already exist. It sends only the changed text as
tool-call output instead of the whole file, which compounds savings as
`nova_api.py`/`nova_orchestrator.py`/`nova_query.py` grow. `old_str` must
match exactly once in the file; if it doesn't, the agent should pick a more
specific `old_str` or fall back to `write_file` for that edit. Reserve
`write_file` for brand-new files. This paragraph is picked up automatically
by `_build_system_prompt()`, which reads all of CLAUDE.md verbatim into the
sub-agent's system prompt every run.

**Token Budget Governor — scoped v1 (2026-07-07, ClickUp `86barhqt9`):**
the finalized spec assumes infrastructure that doesn't exist yet —
`nova_state.db` (its own ClickUp task, `86bara3qe`, is still "to do"), a
push-notification channel into an active Open WebUI chat session, and a
ClickUp-driven task queue with Sonnet/Haiku routing and concurrency slots
(`nova_orchestrator.py` today is one synchronous call per task, no queue,
no daemon, one hardcoded model). Matching the earlier Nova Log v1 decision:
built what's real, explicitly deferred the rest rather than faking it.
**Built:** `nova_token_budget.py` tracks the coding sub-agent's Claude API
consumption (`input + output + cache_creation + cache_read×0.1`, the
finalized formula) against `nova_config.json`'s `token_budget` thresholds,
persisted to a local JSON file (`logs/token_budget_state.json`) in place of
`nova_state.db`. Classifies normal/conservative/critical/halt and folds
into `GET /headroom` via `nova_headroom.py`. `nova_orchestrator.py` checks
the mode at the top of each turn loop and stops cleanly (no further API
call, so no new tool_use is ever proposed) once halted, tags the commit
message, and logs a `budget_halt` outcome to `agent_task_outcomes.jsonl`.
Gated behind `token_budget_governor` in `nova_config.json` (default off).
**Explicitly deferred, not built:** Haiku downgrade (no task-type
classifier exists), task-queue priority-aware selection and concurrency
capping (no task queue exists), Open WebUI push notifications, automatic
ClickUp status updates on halt. `conservative`/`critical` modes are
classified and reported but have no enforced behavioral difference yet —
their spec'd effects all depend on the missing task-queue/classifier.

### Nova Skills Library (2026-07-07, ClickUp `86barguac`)
Structured per-category instruction files (`skills/coding.md`, `retrieval.md`,
`financial.md`, `orchestration.md`, `lore.md`, `memory.md`) that
`nova_orchestrator.py` can prepend to a coding task's context — a compact
skill prompt orients the model with precise conventions instead of the
model re-deriving them from scratch (Nova Reference — Token Efficiency
Strategy v1.0). All six files were already fully drafted in the Nova
Skills Library Index doc; this shipped the wiring, not new content.
**One trim from the literal spec:** category was meant to come from a
ClickUp task's tag, but nothing in Nova's own runtime code reads ClickUp
today (only this interactive session does) — `run_coding_task(task,
category=None)` and `POST /agent/task`'s `category` field take an
explicit caller-supplied string instead. `load_skill()`/`get_skill_version()`
live in `nova_skills.py`; a missing category or skill file is a graceful
no-op, not an error. Each turn's `agent_log.jsonl` entry now carries
`skill_category`/`skill_version` for traceability, per the skill files'
own maintenance note. Gated behind `skill_injection` in `nova_config.json`
(default off).

### Domain State Layer (2026-07-07, ClickUp `86bara3qe`) — scoped v1
Architecture Principles v1.1, Principle 6 distinguishes Chroma (deep
knowledge — lore, documents, past reality) from `nova_state.db` (current
reality — live balances, active projects, system health), and defines 5
domains × 12 entities. **Built:** `nova_state.py` — one generic
`domain_state` table (`domain`, `entity`, `data` JSON, `updated_at`)
covering every domain/entity pair Principle 6 defines, rather than fixed
per-entity columns invented ahead of real data. `write_state`/`get_state`/
`get_domain` are the only interface; adapters are the only intended
writers. `nova_state.db` is local-only, gitignored like `memory/` (never
synced, per Principle 6's data-sensitivity rules). Also built
`nova_state_system.py`, the one adapter with a real, already-existing data
source — wraps `nova_headroom.get_headroom_report()` (now including token
budget) into `system/nova_health` and `system/pending_alerts`.
**Explicitly deferred, each on a real open question, not stubbed:**
`nova_state_financial.py` (needs an approved financial data source — none
named yet, and Principle 6 requires explicit per-connection approval before
one is picked), `nova_state_work.py` / `nova_state_games.py` (both need a
ClickUp API/MCP client inside Nova's own runtime code — nothing in
`nova_tools.py`/`nova_orchestrator.py` gives Nova's own scripts ClickUp
access today, only this interactive session has it), `nova_state_creative.py`
(needs an "art practice log" that doesn't appear to exist under that
description anywhere in the repo or vault). No alert engine (`86bara3qu`)
yet either — Principle 6's own build order sequences it after the
adapters. No refresh scheduler — `refresh_system_state()` is a manual/
future-cron call, since no scheduler infrastructure runs in Nova today
(`nova_watcher.py` itself is still deferred). No new `nova_api.py` route —
nothing reads domain state yet to justify one.

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
Chroma setup (at the time, `PersistentClient(path="C:/Nova/memory")`; both since migrated to
`HttpClient` against the Omen — see Section 2 — but the same "keep every call site's Chroma
config identical" discipline is what made this fix work and still applies). Calling
`get_context_budget("Tell me about Null")` directly returns a 15-file ranked list including
`Null.md`, `Nullius.md`, `Fatale Wildman.md`, and `SYS_Symphony.EXE.md` — matching the expected
post-fix behavior.

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

### Known At-Risk Character Pairs (embedding-distance analysis, 2026-07-12)
Real validation that `/embedding-viz` (Section 1/2, `86bawjg14`) actually predicts blending,
not just looks interesting: computed cosine distance between each character's chunk-embedding
centroid, ranked closest pairs, cross-referenced against real blend events in
`logs/training_flags.jsonl`. **The two closest pairs by embedding distance are exactly the two
most frequent real blend pairs** — Helel↔Luci (distance 0.166, closest overall, 4 real blend
events) and Null↔Nullius (distance 0.193, 2nd closest, **9 real events** — the single most
common blend). Beat↔Rhythm and Aseir↔Helel (both mid-pack close) also each had a real event.

**Watch list — close in embedding space, no blend event yet, but worth monitoring:** Helel↔Raven
(0.278), Aseir↔Luci (0.285), Fatale Wildman↔Marisol (0.303), Aseir↔Raven (0.356). If a future
blend event involves any of these pairs, that's expected, not a new mystery — re-run the
embedding-distance analysis (see `nova_embedding_viz.py`, or query `/embedding-viz` directly)
before assuming it's a new/unrelated bug. Tracked as a watch item, not active work, in
ClickUp `86bawnqdp`.

---

## 7. Nova API Routes

All routes live in `nova_api.py`. Local Aero dev, run with:
```bash
nova-env\Scripts\python -m uvicorn nova_api:app --host 0.0.0.0 --port 8000
```
On the Omen (`192.168.1.250`), the `nova-api` systemd unit runs this on **port 8001** instead —
Chroma's server took 8000 first on that box (see "HP Omen Headless Server" in Section 2).

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
| /nova-log/queries | GET | ✓ Working | Nova Log Query view — recent queries, filterable by category/model/blend |
| /nova-log/benchmarks | GET | ✓ Working | Nova Log Benchmark view — recent golden-query runs, filterable by model |
| /agent/task | POST | ✓ Working | Run a coding task via Nova's coding sub-agent (nova_orchestrator.py) |
| /headroom | GET | ✓ Working | Resource headroom report — VRAM/RAM/CPU + task capacity |
| /embedding-viz | GET | ✓ Working | Embedding-Space Visualization page (HTML) |
| /embedding-viz/data | GET | ✓ Working | Embedding-Space Visualization data (JSON) — optional ?query=, ?refresh= |
| /usage-history | POST | ✓ Working | Merge one machine's daily Claude Code usage aggregate into nova_state.db (system/claude_usage_history) — called by nova_usage_logger.py's SessionEnd hook |
| /usage-history | GET | ✓ Working | Return the merged Claude Code usage history across every machine that's pushed to it |

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
- Check `git status` at session start (see Section 11) and again before ending a session —
  if changes from earlier work are still uncommitted, tell Marvin what's sitting there and
  ask whether to commit. Don't commit unprompted (still requires his go-ahead per the rule
  below), just don't let it go unmentioned.
- When responding to a change request, do three things together in this order:
  1. **Offer to make the change yourself** — don't just describe it and stop.
  2. **Show the specific line(s) the change affects** — with file path and actual lines.
  3. **Explain how and where you would refactor** before applying.
  The goal is to keep Marvin oriented and build troubleshooting intuition — never apply
  a change silently without surfacing what it touches and why.
- **End every response with a low-effort call to action** for what happens next — Marvin
  wants to type/decide as little as possible to keep things moving. Pick whichever format
  actually fits, don't force one:
  - **Lettered options (A/B/C...)** when there are a few clear next steps to choose from.
  - **Yes/no** for a single binary decision.
  - **"Type here:"** style prompt when free-text input is genuinely needed and multiple-choice
    doesn't fit (e.g. a name, a value, a description).
  - If the honest next step actually needs a fuller written answer from him, say that plainly
    instead of forcing a fake menu — don't shrink a real question down to A/B/C just to match
    this format.
  This is the lightweight, end-of-turn version — use the AskUserQuestion tool instead for a
  real branching decision that needs to happen mid-task, not just at the end.

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
| 2026-07-05 | Added a metrics legend to `nova_log.html` (commit `373e03c`) | Marvin asked what blend rate/latency/mismatch counts meant after actually using the dashboard — explains blend rate is a fraction of the 3 fiction golden queries specifically (not all 8), that latency naturally varies run to run, and that the page doesn't auto-refresh |
| 2026-07-06 | Added standalone `nova_mcp_server.py` (commit `db4cf18`) to Sections 1 & 2, and updated Phase 2.5 in Section 1 | Wraps `nova_api.py`'s `/ask` `/graph` `/neighbors` `/context-budget` `/ingest` routes as MCP tools over streamable-http on port 8100 (`mcp`/`httpx` confirmed installed in `nova-env`); not yet wired into `nova_orchestrator.py` or any MCP client, so it's a real v1 building block but currently unused |
| 2026-07-06 | Added prompt caching to `nova_orchestrator.py`'s per-turn Claude calls and `nova_corrector.py`'s `request_correction` (commit `a122edb`) | `nova_orchestrator.py`'s system prompt (CLAUDE.md-sized) is identical every turn of the agent loop — caching it turns turns 2+ into cheap cache reads instead of full-price resends (verified: turn 1 writes a ~10.7K-token cache). `nova_corrector.py`'s lore block recurs across DPO corrections citing the same character file |
| 2026-07-06 | Shipped feature-flag system foundation for Phase 1.75 (`nova_config.py`, `nova_config.json`, commit `5c72787`) to Sections 1, 2 & the Phase Roadmap | Build-first per Nova Reference Section 25F — all flags start off (matches current unaugmented behavior exactly); `config_snapshot()` wired into `nova_log.py`'s per-query telemetry via `nova_query.py`'s `ask()` so `query_log.jsonl` is already tied to exact flag state before any classical-algorithm augment (A*, DP packing, memory decay, etc.) exists to flip one |
| 2026-07-10 | Documented the LangGraph orchestration decision (ClickUp `86bat0u81`, in progress) in Sections 1 & 2 | Decision was made on the ClickUp board (after the original hand-rolled `nova_orchestrator.py` shipped) but never made it into CLAUDE.md; `framework_integrations.langgraph_orchestration` already existed in `nova_config.json` as the gating flag, so no code drift — just a doc catch-up |
| 2026-07-10 | Confirmed and started the HP Omen headless server decision in Phase 4 — created ClickUp `86baw3010` (serverless/raw GPU rental) and `86baw3016` (dedicated GPU machine purchase), commented on `86baeyfm1` | Marvin moved this from backlogged-alternative to active: headless Ubuntu setup proceeds as service host only, with a two-pronged inference plan (serverless GPU rental for Nova's own weights, plus a separate hardware purchase) distinct from the existing hosted-API fallback task (`86baf4eah`) |
| 2026-07-10 | Shipped LangGraph orchestration v1 (`nova_orchestrator_graph.py`, new; `nova_orchestrator.py` edited) to Sections 1 & 2, ClickUp `86bat0u81` | Ports the turn loop that actually exists (single task, no parallelism) rather than the ticket's assumed "sequential + parallel patterns" — that's unbuilt future scope, not this task. New `langgraph` dependency approved by Marvin first. Gated behind `framework_integrations.langgraph_orchestration` (default off, lazy-imported so a missing/broken install can't affect the disabled default path). Verified: same trivial task run through both the old inline loop and the new LangGraph path produced identical `final_status`, turn counts, and `agent_log.jsonl` shape |
| 2026-07-10 | Shipped dynamic model routing mechanism + model-swap eval wrapper (`nova_config.json`/`nova_config.py`/`nova_query.py`/`nova_benchmark.py`) to Phase 3 roadmap, ClickUp `86bagbqk0` & `86bauwqqd` | Routing table's named targets (Phi-4 Mini, Qwen3 8B) aren't adopted locally yet (confirmed via `ollama list` — only `llama3.2`/`llama3.1:8b` pulled; adoption tasks `86bagf51n`/`86bagek35`/`86bara7zk` still backlog), so built the real routing *mechanism* instead (every category → `llama3.2` today), confirmed with Marvin first. Also fixed a real bug found while scoping the eval wrapper: `run_golden_benchmark`'s `model_label` only ever changed the log tag, never the model that actually generated answers — `nova_query.ask()` had no override mechanism at all. Both verified live: routing by temporarily pointing one category at `llama3.1:8b` and confirming the logged model field; the eval wrapper via a real `--evaluate llama3.1:8b` run (pull → real generation → baseline comparison → printed verdict → new log entry), which also caught and fixed a Windows cp1252 subprocess-decode crash on `ollama pull`'s output |
| 2026-07-10 | Shipped Browser Hands harness M1 (`browser_hands/` package, new) to Sections 1 & 2, ClickUp `86barqzmv` | First nested-package structure in the repo (approved) and new `playwright` dependency (approved) — generalized the proven CDP-attach/discover-mode/bounded-timeout/virtualized-tree-walk patterns from the standalone reference script at `C:\Projects\developer_tools\base44_export.py` (outside this repo, not touched). Verified against a real Chrome instance (CDP debug port) and a synthetic local HTML fixture rather than a live site, since no adapter/login exists yet and automating a real login is against the spec's own hard rule. Verification caught and fixed two real bugs: the scrollable-container check only ran once before any content existed to overflow it, silently missing virtualized rows revealed later (now re-checked every pass); and an em-dash in a print statement risked the same Windows cp1252 console crash already hit twice elsewhere tonight (fixed proactively across the new files). M2-M5 (adapters) remain separate, still-backlog tasks |
| 2026-07-11 | Migrated Chroma from local `PersistentClient` to Omen-hosted `HttpClient` across `ingest.py`/`graph_builder.py`/`nova_query.py` (commit `b5f7f68`); added `nova_board.py`/`nova_clickup_client.py` board-maintenance CLI and `nova_chroma_omen_check.py` reachability probe (commit `5d79cf8`), later given `--host`/`--port` overrides (commit `a524623`) | Prerequisite for the Omen actually hosting Chroma (Section 2) — `PersistentClient` has no network protocol at all, this wasn't a config change but a real architecture shift. The board CLI and reachability probe were both built in anticipation of the live Omen setup session, to make board maintenance and infra checks cheaper than round-tripping MCP tools or guessing whether a failure is "not up yet" vs. a real bug |
| 2026-07-11 | Fixed hardcoded Windows path in `nova_orchestrator.py`'s `load_dotenv()` (commit `5146222`) | `dotenv_path="C:/Nova/.env"` silently returned `False` on Linux instead of raising, so the real failure only would have surfaced later as a confusing "env var not set" error once the orchestrator ran on the Omen. Fixed to resolve relative to the script's own location, OS-agnostic |
| 2026-07-12 | Added `nova_status_digest.py` + `NOVA_STATUS.md`/`.nova_status_snapshot.json` board-state digest (commit `3278dec`) to Sections 1 & 2 | One-way board-state handoff: Claude Code writes the digest after sessions that change board state, Claude Chat reads it as a cheap starting point instead of always querying ClickUp fresh |
| 2026-07-12 | Live Omen Ubuntu setup verified through step 9 of 11 (OS, static IP, packages, Chroma data transfer, lid-close, SSH, systemd units, firewall), Chroma reachability confirmed live from the Aero, real port conflict fixed (`nova-api` moved to 8001 on the Omen), Aero-side Ollama-callback groundwork added (`OLLAMA_HOST=0.0.0.0` + `Nova Ollama (Omen callback)` firewall rule) — added to Sections 1, 2, 5, 7 and the Phase 4 roadmap | Doc catch-up after a real SSH session on the Omen plus Windows-side prep on the Aero; only step 10 (Tailscale on the Omen) remains before `86baeyfm1` is done. Also audited the ClickUp board against the session recap rather than trusting it at face value — `86bavtz06` moved from a claimed-but-inaccurate "complete" to "in progress" (real scope is 3 onboarding targets, only 1 done), `86baeyfm1` commented with the verified step-by-step status so it doesn't need re-deriving from prose next session |
| 2026-07-12 | Added an uncommitted-changes check to Sections 8 & 11 — run `git status` at session start and again before ending, tell Marvin what's sitting there | Today's session had real changes (CLAUDE.md doc catch-up, the Ollama-client fix, the Omen runbook) sit uncommitted for a while before Marvin had to ask about them directly — nothing in this file previously said to surface that proactively |
| 2026-07-12 | **HP Omen headless server (`86baeyfm1`) marked complete** — Tailscale live on the Omen (`100.114.197.117`), full Phase 12 end-to-end validation passed (Chroma + `nova_api` reachable on both LAN and Tailscale IPs, Ollama callback confirmed working in both directions including a real curl from the Omen's own shell) — updated Sections 1 & 2, `omen_setup_runbook.md` marked complete | Closes out the runbook Marvin and Claude Chat worked through together this session; `86baf4e29` (Dockerize) is now genuinely unblocked, next real candidate for the coding sub-agent or a direct session |
| 2026-07-12 | Board check of the Nova "in progress" column: documented the Phi-4 Mini/Qwen3 dual-model VRAM NO-GO finding from `86bagek35` in the Phase 3 roadmap line; documented Phase 2's explicit early greenlight (`86baeyg3q`) in the Phase 2 roadmap line, confirmed with Marvin rather than assumed; moved `86baw3016` (GPU purchase) back to "to do" on the board — its own description says nothing is scoped yet, "in progress" overstated real state | Neither finding was in CLAUDE.md despite being real, dated work — the VRAM result directly affects Phase 3 dual-model planning and was sitting only in a ClickUp task body; the Phase 2 exception needed recording so a future session doesn't either silently build on a misread "Backlog" line or refuse work Marvin already authorized |
| 2026-07-12 | Added an end-of-turn call-to-action requirement to Section 8 (lettered options / yes-no / "type here" / plain statement when a real written answer is needed) | Marvin asked to cut down how much he has to type to steer each turn — a low-effort menu at the end of every response, matched to what the actual next decision looks like, instead of open-ended prose he has to respond to freeform every time |
| 2026-07-12 | Shipped `nova_chunk_viz.py` — Chunk Visualization Tool, CLI stage (`86bara3tj`, urgent) — to Sections 1 & 2, Phase 6 roadmap | Task's linked "Section 25" doc turned out to be unrelated (classical-algorithm integration, not this tool) — built from the task's own inline spec instead, which was complete on its own. Deliberately scoped to CLI only (stage 1 of 3, web view/Open WebUI panel deferred) per this project's standing practice of building exactly the real, unblocked slice. Retrieval logic mirrors `nova_query.ask()`'s exact branching (character-filtered vs. graph-scoped) so the tool reflects real production behavior. Verified live against the Omen-hosted Chroma across 6 real queries — character-filtered scoping, `--no-graph` comparison, non-fiction graph-scoped path, and the character-mismatch color signal all confirmed working |
| 2026-07-12 | Shipped `nova_embedding_viz.py`/`.html` — Embedding-Space Visualization (`86bawjg14`, urgent), `GET /embedding-viz`(`/data`) — to Sections 1, 2 & 7, Phase 6 roadmap | Distinct from the same day's chunk-viz CLI (single-query debug vs. whole-corpus cluster audit) — Marvin caught the conflation himself before this got built. Zero new pip dependencies: `sklearn.manifold.TSNE` (already installed) instead of `umap-learn`; rendered as an interactive page matching `nova_log.html`'s exact pattern instead of matplotlib/Artifact, per Marvin's explicit choice. Used the dataviz skill's method throughout — ran `scripts/validate_palette.js` against the reference 8-hue dark palette before shipping it (result: FAILS an all-pairs CVD check on non-adjacent hues, which the palette was never validated for — it's only adjacent-safe; documented as a known, inherent limitation of 8 simultaneously-distinct hues rather than chased with hand-picked replacements, mitigated via shape + always-visible text identity in hover/legend, never color-alone). Verified live: exact point/character counts matched the live Chroma distribution, DPO overlay found a real nonzero set (43 of 479), and a `?query=Tell me about Null` retrieval-hit overlay correctly returned only `Null.md` chunks — mirroring `nova_query.ask()`'s real character-filter branching via reused `nova_chunk_viz.resolve_chunks()` |
| 2026-07-12 | Found and fixed a serious gap in the earlier "Omen COMPLETE" verification (`86bawfn19`): the Omen's git clone was 15 commits stale (missing the actual Chroma HttpClient migration), caused by a passphrase-protected GitHub deploy key silently breaking unattended `git pull`; fixed the key (regenerated with no passphrase), pulled the Omen current, and fixed a second hardcoded-Windows-path bug in `nova_api.py`'s `GRAPH_PATH` — added a new subsection to Section 2 | `/ask` on the Omen was returning fluent, completely hallucinated answers with empty sources/chunks and a `200` status — no error, just wrong. Reachability checks (`200` on `/`, `/headroom`, `/docs`) had never caught this because they don't exercise real RAG behavior. Verified fully fixed: real `collection.count()` of 479, real grounded answers, `/graph` matching the Aero's exact 257 nodes/301 edges |
| 2026-07-12 | Added "Known At-Risk Character Pairs" to Section 6, ClickUp `86bawnqdp` | Real, quantitative validation that the new embedding-viz tool actually predicts production blending, not just looks interesting: the two closest character pairs by embedding centroid distance are exactly the two most frequent real blend pairs in `training_flags.jsonl` (Null↔Nullius, 9 real events, and Helel↔Luci, 4 events). Flagged 4 pairs that are similarly close in embedding space but haven't blended yet (Helel↔Raven, Aseir↔Luci, Fatale Wildman↔Marisol, Aseir↔Raven) as a watch list, so a future blend event on one of these reads as expected, not a new mystery |
| 2026-07-12 | **`86bawfn19` marked complete** — `nova_api.py` deployed to the Omen independent of the Aero, all 4 scope items done including the real test (reached the Omen's `nova-api` over Tailscale with the Aero fully powered off, real grounded answer) — updated Section 2 and the Phase 4 roadmap line | Closes the last real gap in "Nova reachable from my phone independent of the Aero being on." The phone-off test itself was done by Marvin in the prior session; this session re-verified the Omen's `nova-api`/`nova-chroma` systemd units are still active and serving real grounded answers, then updated the board and docs to reflect it |

---

## 11. Session Startup Checklist

At the start of every session, confirm:
1. You have read this file fully.
2. nova_api.py is running (`uvicorn nova_api:app --host 0.0.0.0 --port 8000`)
3. Run `git status` — if there are uncommitted changes from earlier work, tell Marvin what's
   sitting there before starting anything new (see Section 8).
4. You know which task this session is focused on.
5. You are in Plan Mode if the task touches more than one file.

Then say: **"Ready. Working on [task]. Here's what I'm planning to do: [brief plan]."**

Repeat the `git status` check before ending a session, for the same reason — same-session
work is easy to remember to mention, but it's the changes from a prior session that are
most likely to go unmentioned without a deliberate check.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
