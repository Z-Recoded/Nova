# Nova Board Status Digest

Last updated: 2026-07-12 05:33 UTC

One-way snapshot written by Claude Code after sessions that change board
state. Claude Chat reads this as a cheap starting point, not ground truth --
falls back to querying ClickUp directly if it looks stale.

## This session

- Built nova_clickup_client.py + nova_board.py -- ClickUp board CLI enforcing dependency/status house rules (ready/why/check/audit/move/block/link/unlink/split)
- Added --host/--port overrides to nova_chroma_omen_check.py
- Fixed hardcoded Windows path in nova_orchestrator.py dotenv loading (was breaking on the Omen, a Linux box) -- listed other hardcoded C:/ paths across the repo for later triage, not fixed
- Verified Chroma-on-Omen reachability directly; caught and corrected a premature "migration complete" claim -- client code was not actually migrated anywhere (checked the Omen copy of the repo too)
- Migrated nova_query.py, graph_builder.py, ingest.py from local PersistentClient to Omen-hosted HttpClient (192.168.1.250:8000), verified end-to-end against the real server
- Re-triaged 6 tasks to in-progress: RunPod account, MCP credentials, hosted inference vendor pick, GPU purchase decision, Tailscale DERP test, NOVA_STATUS.md digest build
- Confirmed Voice interface (86baeyg3q) stays active/in-progress intentionally
- Built this digest script (86bawbzg1) and generated the first NOVA_STATUS.md

## Changed since last digest

- First digest -- no prior snapshot to compare against.

## In progress (9)

- 86bawbzg1  Build NOVA_STATUS.md digest file
- 86bawbbrx  Resolve MCP credentials (ClickUp/Google/Slack scopes)
- 86bawbbrk  Set up RunPod account + API key
- 86baw3016  Purchase dedicated GPU compute machine from third-party vendor
- 86bat0ue1  Run Tailscale DERP relay reachability test from a genuine remote network
- 86bagek35  Test Phi-4 Mini 128K routing strategy — context fill vs latency + dual VRAM validation
- 86baf4eah  Hosted inference fallback (Groq / Together.ai / Fireworks)
- 86baeyg3q  Voice interface (Whisper + Piper)
- 86baeyfm1  HP Omen Ubuntu headless server setup

## Blocked (13)

- 86baw3010  Rent serverless/raw GPU compute for Nova's own model weights (RunPod/Modal/Vast.ai)
- 86bauwkvq  Token Budget Governor — remaining scope (Haiku routing, queue concurrency, push notifications, auto ClickUp updates)
- 86barue80  Games domain node — nova_state_games.py adapter
- 86barudz2  Work domain node — nova_state_work.py adapter
- 86barex1u  Integrate OpenHands as Nova's coding lane sub-agent (nova-code-agent container)
- 86bara7zk  Source + curate agentic reasoning training data for Qwen3 8B fine-tune
- 86bara7pn  Source + curate coding training data for Nova's coding model
- 86bara3rm  Financial domain node — nova_state_financial.py adapter
- 86bara3qu  Alert engine — proactive state monitoring against nova_state.db
- 86bage4ff  Design & Build: Content Transformation Pipeline (Nova-orchestrated, template-driven)
- 86baf72qq  Docker sub-agent orchestration — ephemeral task containers
- 86baf72n5  MCP tool-calling integration (ClickUp, Drive, Calendar, Slack)
- 86baf4e29  Dockerize Nova services

## Ready (45)

- 86bawf2z2  Design and implement token-based auth for nova_api.py
- 86bawbzbz  Check ClickUp native Automations — dependency-triggered actions
- 86bawbh07  Credential/breach exposure check (Have I Been Pwned)
- 86bawbgne  📌 Reference: Task Dependency & Status Discipline v1.0
- 86bawbfak  Name + approve financial data source (spreadsheet vs. Firefly III)
- 86baux91c  Evaluate Outline — check for redundancy with Obsidian first
- 86baux8y5  Evaluate WebAssembly/WebLLM for a browser-based Nova client
- 86baux8pq  Evaluate Podman for coding sub-agent container hardening
- 86baux89f  Evaluate vLLM for the rented 24GB-tier model serving
- 86baux7zm  Evaluate DSPy for prompt/skill optimization against real outcomes
- 86baux7nd  Evaluate Qdrant as a Chroma alternative (situational only)
- 86baux7bb  Evaluate Chonkie for ingest.py chunking
- 86baux6zb  Evaluate Marker for PDF-to-Markdown corpus ingestion
- 86baux6me  Evaluate Crawl4AI for Browser Hands adapter extraction layer
- 86baux696  Evaluate Instructor for structured LLM output validation
- 86baux60t  Evaluate Ragas for retrieval-quality scoring in the benchmark suite
- 86baux5py  Evaluate LangWatch as Nova Log/benchmark suite complement
- 86baux5c3  Evaluate LiteLLM as Nova's model-routing/gateway layer
- 86bau47mb  [Initiative — not scoped] Omen self-hosted service stack (Coolify, NocoDB, Vaultwarden, Firefly III, and more)
- 86bau2zd9  [Initiative — not scoped] Jack In: three-tier diagnostics (network-resident, disposable external, local voice interface)
- 86batba53  [Initiative — not scoped] Unified Nova presence across all devices (shared memory/identity)
- 86barue4h  Creative domain node — nova_state_creative.py adapter
- 86barr06e  Conform Base44 export script to Browser Hands adapter contract
- 86barr02x  Build subscription-audit browser adapter (login-gated, proves CDP session-reuse at scale)
- 86barqzy8  Fold website audit pipeline into Browser Hands adapter structure
- 86barqztk  Build PiSignage health-check browser adapter (screenshot + drift detection)
- 86bargufp  Evaluate Hermes 3 vs Qwen3 8B for Nova's agentic reasoning lane
- 86bargucp  Evaluate + integrate n8n for domain adapter refresh and alert routing
- 86barby7t  Nova Log rotation — archive entries older than 90 days, keep last 1000 active
- 86barby7m  Build Nova Art Practice Companion module (5 phases)
- 86bara3uj  Spec: Proactive memory — surface connections on ingest
- 86bara3u1  Spec: Temporal awareness layer
- 86bara3tj  Spec + Build: Chunk visualization tool — RAG retrieval audit
- 86bara3pp  Pixel RAG layer — visual retrieval path (CLIP + ColPali)
- 86bagf51n  Re-scope fine-tune pipeline for Phi-4 Mini as Nova's base model
- 86bafvrff  Brute force trail depth test — establish λ and parallel work nominal rates
- 86bafvrek  Implement two-tier memory decay in nova_memory_store.py
- 86bafvrd3  Implement priority queue routing in nova_router.py
- 86bafvrbp  Implement DP context window packing in nova_query.py
- 86bafvrax  Add document-level embeddings to ingest.py for A* heuristic
- 86bafvr98  Implement A* graph traversal for graph-guided retrieval
- 86bafv3b4  Add weighted wikilinks to Obsidian notes for stronger graph edges
- 86bafunj2  Research & Plan: Classical Algorithm Integration into Nova's Decision Layer
- 86baf4e70  RunPod/Vast.ai — cloud GPU for fine-tune runs
- 86baeyfua  Link-aware ingestion upgrade (Option B)
