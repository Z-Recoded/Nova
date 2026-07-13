# Nova Board Status Digest

Last updated: 2026-07-13 03:16 UTC

One-way snapshot written by Claude Code after sessions that change board
state. Claude Chat reads this as a cheap starting point, not ground truth --
falls back to querying ClickUp directly if it looks stale.

## Changed since last digest

- NEW -> ready: 86bawpc67 (Vivarium experiment: bidirectional visual control panel for Nova)
- NEW -> ready: 86bawpc5v (Set up watchdog-based hot-restart pattern for Nova services)
- NEW -> ready: 86bawpc5f (R&D spike: world-model / latent game-state representation)
- NEW -> ready: 86bawp01b (Build session-continuity layer for unified Nova presence)
- NEW -> ready: 86bawntpm (Draft Nova audit process (tool-call log review))
- NEW -> ready: 86bawntpb (Build tool-call logging schema for Nova subagents)
- NEW -> ready: 86bawnqqv (Research: benchmark suites for Nova's agentic/tool-use eval)
- NEW -> ready: 86bawnqdp (Monitor at-risk character pairs for blending (embedding-distance analysis))
- NEW -> ready: 86bawnkfy (Nova Tutor — Phase 7: DPO pairs + fine-tune (3 categories))
- NEW -> ready: 86bawnkf1 (Nova Tutor — Phase 6: Blend-quality flagging log)
- NEW -> ready: 86bawnke6 (Nova Tutor — Phase 5: Cross-domain secondary retrieval)
- NEW -> ready: 86bawnkd9 (Nova Tutor — Phase 4: Synthesis links (Claude-assisted proposal + approval))
- NEW -> ready: 86bawnkcg (Nova Tutor — Phase 3: Domain-aware routing (primary only))
- NEW -> ready: 86bawnkc6 (Nova Tutor — Phase 2: Spaced repetition + quiz engine)
- NEW -> ready: 86bawnkbv (Nova Tutor — Phase 1: Chunk schema + storage)
- NEW -> ready: 86bawna2g (Ingestion Principles addendum — chunking discipline, Tika/Docling rejection, injection-surface note)
- NEW -> ready: 86bawna1k (Router/Governor spec — conditional router as front door to Token Budget Governor)
- NEW -> ready: 86bawna17 (Hybrid Retrieval spec — dense + BM25/FTS5 fusion, cross-encoder reranker)
- NEW -> ready: 86bawm2rb (Spec: Multi-device sensor fusion for spatial reconstruction (echolocation) — with consent/privacy boundaries)
- NEW -> ready: 86bawkz7t (Apply Information Dimensionality & Abstraction Principle to upcoming specs)
- NEW -> ready: 86bawkcha (Spec: Network traversal landscape — rewind + live ambient views for self/subagents)
- NEW -> ready: 86bawk37h (Set up security & code hygiene tooling (ruff, mypy, pre-commit, bandit, detect-secrets, pip-audit))
- in_progress -> ready: 86baw3016 (Purchase dedicated GPU compute machine from third-party vendor)
- blocked -> ready: 86baf4e29 (Dockerize Nova services)
- ready -> in_progress: 86bara3tj (Spec + Build: Chunk visualization tool — RAG retrieval audit)
- NEW -> blocked: 86bawjyj8 (Spec: Subagent escalation ladder + "travel to" central command console)
- ready -> complete/removed: 86bafvr98 (Implement A* graph traversal for graph-guided retrieval)
- in_progress -> complete/removed: 86bawbzg1 (Build NOVA_STATUS.md digest file)
- in_progress -> complete/removed: 86baeyfm1 (HP Omen Ubuntu headless server setup)

## In progress (7)

- 86bawbbrx  Resolve MCP credentials (ClickUp/Google/Slack scopes)
- 86bawbbrk  Set up RunPod account + API key
- 86bat0ue1  Run Tailscale DERP relay reachability test from a genuine remote network
- 86bara3tj  Spec + Build: Chunk visualization tool — RAG retrieval audit
- 86bagek35  Test Phi-4 Mini 128K routing strategy — context fill vs latency + dual VRAM validation
- 86baf4eah  Hosted inference fallback (Groq / Together.ai / Fireworks)
- 86baeyg3q  Voice interface (Whisper + Piper)

## Blocked (13)

- 86bawjyj8  Spec: Subagent escalation ladder + "travel to" central command console
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

## Ready (67)

- 86bawpc67  Vivarium experiment: bidirectional visual control panel for Nova
- 86bawpc5v  Set up watchdog-based hot-restart pattern for Nova services
- 86bawpc5f  R&D spike: world-model / latent game-state representation
- 86bawp01b  Build session-continuity layer for unified Nova presence
- 86bawntpm  Draft Nova audit process (tool-call log review)
- 86bawntpb  Build tool-call logging schema for Nova subagents
- 86bawnqqv  Research: benchmark suites for Nova's agentic/tool-use eval
- 86bawnqdp  Monitor at-risk character pairs for blending (embedding-distance analysis)
- 86bawnkfy  Nova Tutor — Phase 7: DPO pairs + fine-tune (3 categories)
- 86bawnkf1  Nova Tutor — Phase 6: Blend-quality flagging log
- 86bawnke6  Nova Tutor — Phase 5: Cross-domain secondary retrieval
- 86bawnkd9  Nova Tutor — Phase 4: Synthesis links (Claude-assisted proposal + approval)
- 86bawnkcg  Nova Tutor — Phase 3: Domain-aware routing (primary only)
- 86bawnkc6  Nova Tutor — Phase 2: Spaced repetition + quiz engine
- 86bawnkbv  Nova Tutor — Phase 1: Chunk schema + storage
- 86bawna2g  Ingestion Principles addendum — chunking discipline, Tika/Docling rejection, injection-surface note
- 86bawna1k  Router/Governor spec — conditional router as front door to Token Budget Governor
- 86bawna17  Hybrid Retrieval spec — dense + BM25/FTS5 fusion, cross-encoder reranker
- 86bawm2rb  Spec: Multi-device sensor fusion for spatial reconstruction (echolocation) — with consent/privacy boundaries
- 86bawkz7t  Apply Information Dimensionality & Abstraction Principle to upcoming specs
- 86bawkcha  Spec: Network traversal landscape — rewind + live ambient views for self/subagents
- 86bawk37h  Set up security & code hygiene tooling (ruff, mypy, pre-commit, bandit, detect-secrets, pip-audit)
- 86bawf2z2  Design and implement token-based auth for nova_api.py
- 86bawbzbz  Check ClickUp native Automations — dependency-triggered actions
- 86bawbh07  Credential/breach exposure check (Have I Been Pwned)
- 86bawbgne  📌 Reference: Task Dependency & Status Discipline v1.0
- 86bawbfak  Name + approve financial data source (spreadsheet vs. Firefly III)
- 86baw3016  Purchase dedicated GPU compute machine from third-party vendor
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
- 86batba53  Unified Nova presence across all devices (shared memory/identity)
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
- 86bara3pp  Pixel RAG layer — visual retrieval path (CLIP + ColPali)
- 86bagf51n  Re-scope fine-tune pipeline for Phi-4 Mini as Nova's base model
- 86bafvrff  Brute force trail depth test — establish λ and parallel work nominal rates
- 86bafvrek  Implement two-tier memory decay in nova_memory_store.py
- 86bafvrd3  Implement priority queue routing in nova_router.py
- 86bafvrbp  Implement DP context window packing in nova_query.py
- 86bafvrax  Add document-level embeddings to ingest.py for A* heuristic
- 86bafv3b4  Add weighted wikilinks to Obsidian notes for stronger graph edges
- 86bafunj2  Research & Plan: Classical Algorithm Integration into Nova's Decision Layer
- 86baf4e70  RunPod/Vast.ai — cloud GPU for fine-tune runs
- 86baf4e29  Dockerize Nova services
- 86baeyfua  Link-aware ingestion upgrade (Option B)
