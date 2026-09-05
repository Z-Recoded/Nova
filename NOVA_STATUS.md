# Nova Board Status Digest

Last updated: 2026-09-05 05:14 UTC

One-way snapshot written by Claude Code after sessions that change board
state. Claude Chat reads this as a cheap starting point, not ground truth --
falls back to querying ClickUp directly if it looks stale.

## Changed since last digest

- NEW -> ready: 86bbvcbcv (Nova Tutor — Overworld Phase 6: Interaction logging (multimodal training-signal capture))
- NEW -> ready: 86bbvcbcp (Nova Tutor — Overworld Phase 5: Procedural trial rooms (coding + hand-derivation))
- NEW -> ready: 86bbvcbby (Nova Tutor — Overworld Phase 0: Schema extension (modality + visual_refs fields))
- ready -> complete/removed: 86bb53hmk (Fix latent ThinkingBlock bug in nova_task_queue.propose_tier())

## In progress (0)

- none

## Blocked (13)

- 86bbnbq0q  Scope Qwen3.6/3.8-27B dense as a coder-specialist candidate for the local 3090 rig
- 86bbaph6w  Spec: Local compute sizing & multi-GPU build plan (Nova central node)
- 86bawjyj8  Spec: Subagent escalation ladder + "travel to" central command console
- 86bauwkvq  Token Budget Governor — remaining scope (Haiku routing, queue concurrency, push notifications, auto ClickUp updates)
- 86barue80  Games domain node — nova_state_games.py adapter
- 86barudz2  Work domain node — nova_state_work.py adapter
- 86barex1u  Integrate OpenHands as Nova's coding lane sub-agent (nova-code-agent container)
- 86bara7zk  Source + curate agentic reasoning training data for Qwen3 8B fine-tune
- 86bara3rm  Financial domain node — nova_state_financial.py adapter
- 86bara3qu  Alert engine — proactive state monitoring against nova_state.db
- 86bage4ff  Design & Build: Content Transformation Pipeline (Nova-orchestrated, template-driven)
- 86baf72qq  Docker sub-agent orchestration — ephemeral task containers
- 86baf4e70  RunPod/Vast.ai — cloud GPU for fine-tune runs

## Ready (125)

- 86bbvcbcv  Nova Tutor — Overworld Phase 6: Interaction logging (multimodal training-signal capture)
- 86bbvcbcp  Nova Tutor — Overworld Phase 5: Procedural trial rooms (coding + hand-derivation)
- 86bbvcbby  Nova Tutor — Overworld Phase 0: Schema extension (modality + visual_refs fields)
- 86bbvc3wr  Budget available for coding-specialist model testing/rented compute
- 86bbn80qp  Model capability complexity rubric + boundary visualization
- 86bbkru66  Multi-agent scaling limits: where does specialist-squad coordination hit diminishing returns?
- 86bbkr4aa  Hands-on: TransformerLens mechanistic interpretability exploration
- 86bbkr48x  Task-complexity framework: analytical + empirical, state kept external to models
- 86bbk2nkb  OmniRoute as squad-bootstrap infra on the Omen (non-training-critical roles, with provider provenance tracking)
- 86bbk1kqr  Explore self-play / asymmetric self-play for generating coder specialist training problems
- 86bbjzmhp  Claude Code session as live teacher-student loop for coder specialist (hooks-based)
- 86bbjrt4d  Nova internal shape visualizer: UMAP on Chroma chunks + cross-specialist RSA
- 86bbjrjtc  Code World Model: Generate/Improve/Fix search loop for coder specialist
- 86bbjr89r  Squad-host KV cache persistence: llama-server slot save/restore + reverse proxy
- 86bbh41rk  Explore: small proxy-model validation gate for training recipe changes
- 86bbh0ad9  Explore: oracle-based supplemental signal for nova_corrector.py
- 86bbfwm3y  Explore task-familiarity as a routing signal for nova_task_queue.propose_tier()
- 86bbfw011  Eval Harness — Initiative 9: human verification UX (routed attention, not full review)
- 86bbezc3e  Eval Harness — Initiative 8: correlation-based failure prediction
- 86bbezc34  Eval Harness — Initiative 7: accumulated-failure tracking (integral signal)
- 86bbch9cy  Coding specialist: explore structure-aware (AST-level) action primitives
- 86bbch9ak  Coding specialist: define verifiable task template (narrow scope + objective check)
- 86bbch95y  Coding specialist: design constrained action-space interface (ACI)
- 86bbcfvay  Eval Harness — Initiative 5: per-specialist vetting harness
- 86bbcfvak  Eval Harness — Initiative 4: guardrail — verifier stays inference-time only
- 86bbcfva4  Eval Harness — Initiative 3: add inference-time verifier
- 86bbcfpd1  Nova Training Pipeline — Phase 5: hybrid verification at inference time
- 86bbcfpck  Nova Training Pipeline — Phase 4: DPO difficulty filtering
- 86bbcfpbg  Nova Training Pipeline — Phase 2: grounded execution-based refinement (per module)
- 86bb77vk6  Completion gate misses cross-module circular imports (found live in 86bb71a0f re-eval, task 6)
- 86bb73bjz  Screen BF16/FP8 vs AWQ INT4 on held-out eval before committing to AWQ as production format
- 86bb72wfy  Test tool_choice="required" to stop over-explaining (verify against known Qwen3+reasoning parser bug)
- 86bb72gpa  Coding agent runs its own verification repeatedly but never fixes the bug it finds
- 86bb728nj  RunPod coding agent re-issues already-successful edits, burns full 25-turn budget without progressing
- 86bb71x1j  Design auto-configuring harness: detect and align tool-call/completion conventions per model backend
- 86bb71a15  Verify aero_only scoping on framework_integrations.runpod_coding_agent is policy, not a hard dependency
- 86bb6ruqb  Research: Directed/targeted audio output channel (parametric & beamforming speakers)
- 86bb639k9  📌 Reference: Task Planning Standard v1.0
- 86bb52gc5  Explore: Unified failure-state dashboard (Vivarium + chunk-viz + agent-run pane)
- 86bb4e29m  Evaluate Pushover as ntfy replacement for push notifications
- 86bb3r0z4  Evaluate Pocket (heypocket) MCP server as a Nova / life-autosave ingestion source
- 86bb3qvpa  Integrate slop_linter.py into Nova pipeline (tight mode / STE-100 style check)
- 86bb3pgnc  Marimo Adoption — Reactive Notebook Tooling
- 86bb3p8xw  Automation Handover Package — Reusable Template
- 86bb3ceyp  Nova Controller: push notifications (Layer 3)
- 86bb3ceym  Nova Controller: pre-action approval gate (policy-based, not agent-judgment)
- 86bb1nu7u  Prototype TabFM classifier for Nova blend-detection + routing gates
- 86bb0wp16  Spec: Fine-Tune CI/CD Pipeline (data-volume-triggered retraining)
- 86bb0we34  Evaluate Karakeep for tagged/indexed social content archive (content pipeline + Nova ingestion)
- 86bb03ap2  Board hygiene scanner: flag deviations from task/doc conventions
- 86bb01yn7  Spec: Runaway-process detection (extend nova_headroom.py)
- 86bb01kzy  Explore: bridging embedding-space gaps between lore clusters
- 86bb00zcx  Generalize nova_logger/nova_corrector into shared "HealLoop" service
- 86bb00yhq  Nova chat QoL: word-weight semantic highlighting
- 86baxzadt  RF/SDR sensor layer for Nova: ambient data feed (Tier 1) + RF security (Tier 2)
- 86baxw8jw  [Initiative — not scoped] Migrate off ClickUp to local-first PM tool (NocoDB likely)
- 86baxv3yp  Board hygiene retrofit: custom fields, tag cleanup, link reclassification
- 86baxuq12  Self-hosted git remote (Gitea/Forgejo) on the Omen
- 86baxu9u9  Rollback/safety boundary: chain-level revert for multi-step autonomous runs
- 86baxtb4m  Migrate Nova design docs: Google Drive → self-hosted Obsidian vault
- 86baxkrzq  Spatializing networks: the internet as an explorable place (long-horizon exploration)
- 86baxc3xy  Harness pruning review (periodic — cut what no longer earns its place)
- 86baxc3xh  Modular/swappable harness architecture (charter vs. task-specific control logic)
- 86baxbmh3  Nova security posture — consolidated review (standing task, revisit on infra changes)
- 86baxbeuc  Tactical practice-mode LLM opponent (PokéChamp-style, turn-based grid tactics)
- 86baxb10x  Visual sequence capture: lightweight frame/video recording for agent + game sessions
- 86bax8bb5  Capability–understanding differential scorer (Nova vs. Marvin)
- 86bawx7vj  Headless Nova coding: usage-aware autonomous runner + branch-based diff confirmation
- 86bawpvzz  [Initiative — not scoped] Autonomous coding sessions — Nova writes its own code to build local-model training data
- 86bawpc67  Vivarium experiment: bidirectional visual control panel for Nova
- 86bawpc5v  Set up watchdog-based hot-restart pattern for Nova services
- 86bawp01b  Build session-continuity layer for unified Nova presence
- 86bawntpm  Draft Nova audit process (tool-call log review)
- 86bawnqqv  Research: benchmark suites for Nova's agentic/tool-use eval
- 86bawnqdp  Monitor at-risk character pairs for blending (embedding-distance analysis)
- 86bawnkc6  Nova Tutor — Phase 2: Spaced repetition + quiz engine
- 86bawna2g  Ingestion Principles addendum — chunking discipline, Tika/Docling rejection, injection-surface note
- 86bawna1k  Router/Governor spec — conditional router as front door to Token Budget Governor
- 86bawna17  Hybrid Retrieval spec — dense + BM25/FTS5 fusion, cross-encoder reranker
- 86bawm2rb  Spec: Multi-device sensor fusion for spatial reconstruction (echolocation) — with consent/privacy boundaries
- 86bawkz7t  Apply Information Dimensionality & Abstraction Principle to upcoming specs
- 86bawkcha  Spec: Network traversal landscape — rewind + live ambient views for self/subagents
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
- 86baux6zb  Evaluate Marker for PDF-to-Markdown corpus ingestion
- 86baux6me  Evaluate Crawl4AI for Browser Hands adapter extraction layer
- 86baux696  Evaluate Instructor for structured LLM output validation
- 86baux60t  Evaluate Ragas for retrieval-quality scoring in the benchmark suite
- 86baux5c3  Evaluate LiteLLM as Nova's model-routing/gateway layer
- 86bau47mb  [Initiative — not scoped] Omen self-hosted service stack (Coolify, NocoDB, Vaultwarden, Firefly III, and more)
- 86bau2zd9  [Initiative — not scoped] Jack In: three-tier diagnostics (network-resident, disposable external, local voice interface)
- 86batba53  Unified Nova presence across all devices (shared memory/identity)
- 86barue4h  Creative domain node — nova_state_creative.py adapter
- 86barr06e  Conform Base44 export script to Browser Hands adapter contract
- 86barr02x  Build subscription-audit browser adapter (login-gated, proves CDP session-reuse at scale)
- 86bargufp  Evaluate Hermes 3 vs Qwen3 8B for Nova's agentic reasoning lane
- 86bargucp  Evaluate + integrate n8n for domain adapter refresh and alert routing
- 86barby7m  Build Nova Art Practice Companion module (5 phases)
- 86bara3uj  Spec: Proactive memory — surface connections on ingest
- 86bara3u1  Spec: Temporal awareness layer
- 86bara3tj  Spec + Build: Chunk visualization tool — RAG retrieval audit
- 86bara3pp  Pixel RAG layer — visual retrieval path (CLIP + ColPali)
- 86bafvrff  Brute force trail depth test — establish λ and parallel work nominal rates
- 86bafvrek  Implement two-tier memory decay in nova_memory_store.py
- 86bafvrd3  Implement priority queue routing in nova_router.py
- 86bafvrbp  Implement DP context window packing in nova_query.py
- 86bafvrax  Add document-level embeddings to ingest.py for A* heuristic
- 86bafv3b4  Add weighted wikilinks to Obsidian notes for stronger graph edges
- 86bafunj2  Research & Plan: Classical Algorithm Integration into Nova's Decision Layer
- 86baf72n5  MCP tool-calling integration (ClickUp, Drive, Calendar, Slack)
- 86baf4eah  Hosted inference fallback (Groq / Together.ai / Fireworks)
- 86baf4e29  Dockerize Nova services
- 86baeyg3q  Voice interface (Whisper + Piper)
- 86baeyg1h  First fine-tune pass (Unsloth + DPO → GGUF → Ollama)
- 86baeyfua  Link-aware ingestion upgrade (Option B)
