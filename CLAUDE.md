# CLAUDE.md — Nova Project Context & Coding Standards
> Read this fully before writing a single line. This file is the source of truth for how we work.

---

## 1. Project Overview

Nova is a personal persistent AI system built around Marvin's Second Brain (Obsidian vault). It
is a local-first RAG system with a knowledge graph layer, FastAPI backend, and a training
pipeline for fine-tuning. Nova is not a product — it is a personal tool built incrementally,
one stable phase at a time.

> **Full build history:** the "why" behind every file, every incident, and the complete dated
> change log live in `NOVA_BUILD_LOG.md` (repo root) — read it when you need the backstory
> behind a decision. This file (CLAUDE.md) stays current-facts-only. The file-by-file inventory
> lives in Section 2's "File Locations" tree below, not duplicated here.

### Current Phase: Phase 1 — Memory Core (active)
Nova v0.1 is operational — all core pipeline files (`ingest.py`, `nova_query.py`,
`nova_router.py`, `nova_api.py`, `graph_builder.py`, `nova_logger.py`, `nova_corrector.py`,
`nova_chat.py`, `nova_memory_store.py`, `nova_benchmark.py`, `nova_log.py`) are built and
validated. See Section 2's file tree for the full current inventory.

### Phase Roadmap
- Phase 0    | Foundation             | ✓ Complete
- Phase 1    | Memory Core            | ✓ Operational — Nova Log Health/Query/Benchmark views live; Pipeline view blocked on Phase 6 content pipeline; log rotation deferred
- Phase 1.5  | Self-Monitoring        | ✓ v1 live — `nova_headroom.py` (`GET /headroom`), `nova_benchmark.py --golden` (Llama 3.2 3B baseline established, doubles as Phase 3/3.5 eval harness). Task Scheduler auto-start removed 2026-07-29 (Marvin no longer wants it) — see Phase 4
- Phase 1.75 | Retrieval Intelligence | Backlog, foundation laid — feature-flag system ✓ v1 live (`nova_config.py`/`.json`, all off). Remaining: A* graph traversal, DP context-window packing, priority-queue routing, two-tier memory decay, weighted wikilinks — each gates on a flag already defined
- Phase 2    | Voice & Capture        | **Explicitly greenlit early, in progress** (`86baeyg3q`, confirmed with Marvin 2026-07-12) — Minimal tier (wake word + local STT/TTS) live-verified 2026-07-19. Deliberate exception to "don't build Phase 2+ without explicit instruction" below — confirm with Marvin before resuming after a gap
- Phase 2.5  | Agent Layer            | Backlog — file CRUD ✓ v1 live (`nova_tools.py`); Nova MCP Server ✓ v1 live, unwired (port 8100); Browser Hands harness M1 ✓ v1 live (`browser_hands/`, CDP-attach only, no login — hard rule); Docker sub-agent orchestration deferred, see Phase 3.5
- Phase 3    | First Fine-Tune        | Backlog — Unsloth + DPO → GGUF → Ollama. **Base-model re-eval DONE 2026-07-21 — verdict: stay on Llama 3.2 3B**, no candidate (llama3.1:8b, phi4-mini, qwen3:8b, gemma3:4b) beat the logged baseline on Nova's own golden-query suite. Swap trigger: a candidate must clearly beat the baseline, re-running `nova_benchmark.py --golden`, not a fixed timeline. Fine-tune pipeline built for Phi-4 Mini (`nova_finetune_phi4.py`, Unsloth QLoRA DPO, trains on the Aero's RTX 5070) — verified live with 3 real DPO steps, but `run()` hard-refuses below `MIN_REAL_PAIRS = 100` (currently ~33 real pairs, still accumulating). Full backstory in NOVA_BUILD_LOG.md
- Phase 3.5  | Coding Agent Lane      | ✓ v1 live (2026-07-05) — Claude API-backed coding sub-agent (`nova_orchestrator.py`), git-worktree isolated, no Docker/OpenHands yet. LangGraph orchestration v1 live, gated off (`nova_orchestrator_graph.py`). **Qwen3 8B swap trigger:** ~30-50 diverse task transcripts in `agent_log.jsonl` (currently ~29-31 combined Aero+Omen, see `/qwen-swap-status`), 20% held out, swap only once Qwen3 clears a pass bar against Claude's baseline on the same tasks
- Phase 4    | Roaming Layer          | ✓ Lightweight v1 shipped — Tailscale. **HP Omen headless Ubuntu server (`86baeyfm1`) — ✓ COMPLETE** (full story in NOVA_BUILD_LOG.md). **`nova_api.py` deployed to the Omen (`86bawfn19`) — ✓ COMPLETE** — reachable over Tailscale independent of the Aero being on. Task Scheduler auto-start (the "Nova Auto-Start" task running `start_nova.ps1` on Aero login) removed 2026-07-29 — Marvin no longer wants the Aero auto-starting nova_api.py/Open WebUI on login; `start_nova.ps1` itself is untouched on disk for manual use
- Phase 5    | Continuous Learning    | Backlog — quarterly fine-tune cycles
- Phase 6    | Domain Expansion       | Backlog, foundation laid — `nova_state.db` schema + system adapter ✓ v1 live; financial/work/creative/games adapters blocked on real open questions. Chunk viz (`nova_chunk_viz.py`) + embedding-space viz (`nova_embedding_viz.py`/`.html`, `GET /embedding-viz`) ✓ v1 live. Also backlog: pixel RAG, temporal awareness, proactive memory, content transformation pipeline

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
├── nova_log_rotation.py    # Weekly rotation for the Nova Log telemetry files — archive >90d + keep last 1000, non-destructive (86barby7t)
├── nova_sources.py         # Source paths config — Second Brain location
├── nova_tools.py           # Path-scoped file/exec primitives for the coding sub-agent
├── nova_orchestrator.py    # Coding sub-agent loop (Claude-backed v1, git-worktree isolated)
├── nova_orchestrator_graph.py # LangGraph port of the turn loop (langgraph_orchestration flag, default off)
├── nova_orchestrator_runpod.py # RunPod (Qwen2.5-Coder-32B) backend for the turn loop — prompted tool-call format, 4 guards (runpod_coding_agent flag, default off)
├── nova_coding_eval.py     # Human-graded held-out comparison harness — reruns historical merged tasks against a backend under test
├── nova_coding_corrector.py # Claude-written chosen_diff corrections for coding_review_log.jsonl (mirrors nova_corrector.py) — DPO pair source for the Qwen coding fine-tune
├── nova_finetune_qwen_coder_sft.py # SFT warm-start stage for Qwen2.5-Coder-32B (KodCode-V1 + Nova's own coding_review_log.jsonl) — assumes a rented A100, exports merged safetensors
├── nova_finetune_qwen_coder.py # DPO refinement stage for Qwen2.5-Coder-32B — same hardware assumption, warm-starts from the SFT stage's output if available
├── nova_hf_upload.py       # Shared helper — pushes a locally-merged checkpoint to a private HF Hub repo so a rented pod can stop right after training
├── nova_runpod_pod_launch.py # Launch/stop helper for a rented RunPod A100 pod (86baf4e70 Pattern 1) — provisioning only, never bootstraps training itself (tier-manual-only)
├── nova_config.py          # Feature-flag reads (is_augment_enabled, config_snapshot, etc.)
├── nova_config.json        # Feature-flag values — all off today (Phase 1.75 gating)
├── nova_mcp_server.py      # Standalone MCP server wrapping nova_api.py routes (unwired, port 8100)
├── nova_chroma_omen_check.py # Chroma-on-Omen reachability probe (TCP → heartbeat → collection → real query)
├── nova_usage_logger.py    # Local Claude Code usage/cost history + activity profile (scans ~/.claude/projects/**/*.jsonl, all projects)
├── nova_tool_call_log.py   # Tool-call logging schema for the coding sub-agent (interim — Langfuse will absorb this)
├── nova_omen_dispatch.py   # Headless task dispatch on the Omen via `claude -p --worktree` over SSH, plus resume_headless_task() for answered escalations (86bax0exx invocation step, 86bax0wkj)
├── nova_escalation.py      # Real escalation-block parsing + pause-at-will switch for headless dispatch (86bax0exx step 5, 86bax0wkj)
├── nova_controller.html    # Nova Controller Feed — served at /controller, PWA-installable (86baxahn7, supersedes nova_escalations.html)
├── manifest.json, sw.js, icon-192.png, icon-512.png  # PWA manifest/service worker/icons for nova_controller.html
├── nova_notify.py          # Thin ntfy.sh push-notification wrapper — Layer 3 of 86baykvb7's deferred real-push design (86bb3ceyp)
├── nova_headless_approval_hook.py # Claude Code PreToolUse hook — headless-lane pre-action approval gate, wired via .claude/settings.json (86bb3r0h4)
├── nova_omen_sync.py       # One-command sync for the Omen's main checkout — git pull, restart nova-api/nova-chroma, verify listening
├── nova_task_queue.py      # Readiness detection + task resolution for headless dispatch (86bax0exx steps 1-2)
├── nova_scheduled_dispatch.py # Cron-fired entry point on the Omen — picks + dispatches one autonomy-safe-tagged task every 2 hours; also owns abort_current_dispatch() (86bb3ceyj)
├── nova_agent_log_status.py # Read-only Aero+Omen agent_log.jsonl merge — Qwen3 8B swap-trigger progress check
├── nova_worktree_status.py # Read-only Aero+Omen git worktree inventory (age/merged/prunable) — Controller worktree browser
├── nova_worktree_pr.py     # Diff-preview-and-merge for dispatched tasks — pushes a real GitHub PR + a discard action, no custom diff viewer (86bb3ceyf)
├── nova_training_data_status.py # Cross-machine DPO pair count backing /training-data-status — same combined/omen_only/aero_only pattern as nova_agent_log_status.py; also owns dispatch_remote_patch() for the write-side bridge
├── nova_training_flags_patch.py # Shared, single-file-scoped patch logic for one training_flags.jsonl entry — used both locally and by the remote CLI wrapper
├── nova_patch_training_flags_cli.py # stdin/stdout JSON wrapper around nova_training_flags_patch.py, invoked over the command-restricted write SSH key
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
├── runpod_finetune_runbook.md # Manual steps once a rented A100 pod is RUNNING — clone, install, run both finetune stages, verify upload, terminate
├── memory/                 # Legacy local Chroma PersistentClient data — superseded by the Omen-hosted
│                           # HttpClient server (see Key External Dependencies below), kept as-is, not deleted
└── logs/
    ├── query_log.jsonl          # Per-query telemetry (nova_log.py) — Nova Log Health data source
    ├── agent_log.jsonl          # Per-turn coding sub-agent telemetry (nova_orchestrator.py)
    ├── agent_task_outcomes.jsonl # Merged/discarded label per branch (nova_orchestrator.record_task_outcome) — call by hand after each merge/discard decision; this is what turns agent_log.jsonl into a usable Qwen3 training set later
    ├── coding_review_log.jsonl # (diff, verdict) pairs from the RunPod-lane review pass (nova_orchestrator._log_coding_review) — future Qwen fine-tune training data
    ├── runpod_cost_log.jsonl   # Per-task real dollar cost for the RunPod coding lane (nova_orchestrator_runpod._log_runpod_cost_summary)
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
- **Chroma** — Omen-hosted via `chromadb.HttpClient(host=..., port=8000)`, not the local
  `PersistentClient` this file originally documented. **As of 2026-07-19, `CHROMA_HOST` is
  the Omen's Tailscale IP (`100.114.197.117`), not its LAN IP (`192.168.1.250`)** — the LAN
  IP only resolves from the home subnet, and a real failure was hit live (any script
  importing `nova_query`/`graph_builder`/`ingest` crashed at import time whenever the Aero
  wasn't on the home network, even though the Omen itself was up and reachable the whole
  time over Tailscale). All three call sites (`ingest.py`, `graph_builder.py`,
  `nova_query.py`) use identical host/port/collection/embedding-function config — same
  discipline as the original `/context-budget` fix (Section 5). `C:/Nova/memory/` still
  exists on disk but is legacy — no script reads it anymore. See "HP Omen Headless Server"
  below
- **Collection name** — `nova_memory`
- **Embedding function** — `DefaultEmbeddingFunction()` from `chromadb.utils`
- **Claude API** — used by `nova_corrector.py` for DPO pair generation, and by
  `nova_orchestrator.py` as the interim coding sub-agent brain (see below)

### HP Omen Headless Server (ClickUp `86baeyfm1`) — ✓ COMPLETE (2026-07-12)
Always-on Ubuntu service host (Chroma, `nova_state.db`, orchestration) for the Aero, which
sleeps. Service-host-only, not inference-capable (GTX 1050 Ti, no driver installed). Ubuntu
24.04, static IP `192.168.1.250`, Tailscale IP `100.114.197.117` (hostname `nova`). Chroma runs
as `HttpClient` server on port 8000; `nova-api` runs on **port 8001** (not 8000 — real port
conflict, both services defaulted there). `nova-chroma`/`nova-api` are permanent systemd units.
Full step-by-step commands in `omen_setup_runbook.md`. Full incident narrative (stale-clone
discovery, passphrase-protected deploy key, hardcoded-path bugs) in `NOVA_BUILD_LOG.md`.

**Key lesson from `86bawfn19`'s verification gap:** "reachable" (HTTP 200) and "functionally
correct" are different claims — a route can fail open (wrong data, no error) rather than fail
loud. Always verify the payload, not just the status code, especially after deploying to the
Omen. `nova_api.py` on the Omen is now COMPLETE and verified — reachable over Tailscale
independent of the Aero being powered on (no Open WebUI on the Omen, raw API only).

### Working Directly on the Omen via SSH (away from the Aero)
When SSHed into the Omen to do real work away from the Aero — a real interactive/manual
session, not the automated `nova_omen_dispatch.py` headless path — **never edit the main
checkout (`~/nova`) directly.** That directory is a one-directional deployment target:
`nova_omen_sync.py` pulls into it and restarts `nova-api`/`nova-chroma`. Hand-editing and
pushing from there recreates the exact two-way drift that caused the 15-commit stale-clone
incident (full story in `NOVA_BUILD_LOG.md`, "Important gap..." under `86bawfn19`).

Instead, use the same worktree discipline `nova_orchestrator.py` already uses for headless
dispatch — `origin` is the only source of truth, every worktree fetches fresh from it, the
main checkout only ever receives, never originates:

1. `git worktree add ~/nova-work/<task-name> -b <branch> origin/master` — fetches fresh from
   `origin`, ignores whatever state the main checkout happens to be in.
2. Do the work there.
3. **Commit on the Omen, but push from the Aero — the Omen's GitHub deploy key is read-only
   by design and cannot push, confirmed live 2026-07-25** (`git push` from the Omen fails
   with "The key you are authenticating with has been marked as read only"). This is
   deliberate, not a bug to route around by widening the key: an always-on, internet-reachable
   headless box having direct write access to the repo is real added blast radius if it's ever
   compromised, and nothing about the Omen's role requires it. The real flow:
   - Commit the worktree's changes on the Omen as usual.
   - From the Aero: `git fetch ssh://<user>@<omen-tailscale-ip>/home/<user>/nova
     <branch>:<branch>` — pulls the commit directly out of the Omen's local object store, no
     GitHub round-trip needed for this step.
   - `git push origin <branch>` from the Aero, which has real write access.
4. Merge to `master` from wherever's convenient (GitHub's web UI, or `gh pr merge` from the
   Aero) — never directly on the Omen, for the same read-only-key reason.
5. If the change touches what `nova-api`/`nova-chroma` actually run, trigger
   `nova_omen_sync.py` (or `git pull` + restart directly, since you're already on the Omen)
   so the live services pick it up.
6. Back on the Aero next session: `git pull` before starting new work there — same discipline
   as any second machine touching a shared repo.

**Two gotchas worth knowing before they look like a mystery failure:** no git identity is
configured on the Omen's main repo — a commit made directly on the Omen needs `git -c
user.name='...' -c user.email='...' commit ...`; and a plain `ssh host "command"` doesn't source
`.bashrc`, so PATH is missing `~/.local/bin` (breaks `gitleaks`'s pre-commit hook) — prepend
`PATH=$HOME/.local/bin:$PATH` explicitly in the SSH command.

Once the Nova Controller exists, this manual SSH workflow is expected to mostly be replaced by
triggering `nova_omen_dispatch.py` from the Controller UI, which already self-syncs from
`origin` and never needed the Omen to push in the first place.

### Omen Capacity Audit (86baxty6d, self-hosting gate) — 2026-07-21
Gate: no further self-hosting tasks proceed until `nova_omen_capacity.py` (SSHes from the Aero,
real CPU/RAM/disk/GPU snapshot, logs to `logs/omen_capacity_log.jsonl`) confirms headroom —
revisited periodically as new services get proposed, not just once. **Verdict as of 2026-07-21:
gate open, large headroom** (8 cores near-idle, 84% RAM free, 77% disk free) — mostly because
almost nothing is deployed yet (only `nova-api`/`nova-chroma` are persistent; Docker installed
but empty). GPU present (GTX 1050 Ti) but no driver installed, not a capacity factor.
**Recommendation, not yet acted on:** re-run before/after each self-hosting task deploys,
watching RAM specifically — smallest of the three pools, most likely to get pressured first by
a multi-service stack like Langfuse's. Full findings in `NOVA_BUILD_LOG.md`.

### Nova Coding Sub-Agent (nova_orchestrator.py)
Nova can now write to its own codebase — the one sanctioned exception to a human
surfacing every change before it's applied (Section 8). Safety comes from **git worktree
isolation**, not manual review of each write: every task runs in its own disposable
worktree + branch under `C:/nova-agent-worktrees/`, never the live `C:/Nova` tree.
`nova_orchestrator.py` never merges or deletes a worktree — Marvin always reviews the
diff and merges by hand. v1 is driven by the Claude API (not a local model yet) and has
no Docker/OpenHands sandboxing for the interactive lane — that's deferred; see Phase 3.5.
LangGraph orchestration (`nova_orchestrator_graph.py`) is available as an alternate turn
loop, gated behind `framework_integrations.langgraph_orchestration` (default off).

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

**RunPod backend (`nova_orchestrator_runpod.py`, 2026-07-27):** an alternate turn loop using
Nova's RunPod-hosted Qwen2.5-Coder-32B-Instruct-AWQ endpoint instead of Claude, gated behind
`framework_integrations.runpod_coding_agent` (default off, `aero_only`). This endpoint has no
native tool-calling API, so tool calls are requested via a prompted `<tools>{...}</tools>` text
format and parsed out of the plain-text response. Three pre-dispatch guards prevent observed
failure loops from the 2026-07-27 held-out eval (`project_qwen3_coding_spike_result.md`):
refusing `write_file`/`file_replace` on an unread existing path, refusing a repeat `read_file` on
an already-read path, and refusing an exact repeat of an already-failed call. A fourth,
**post**-dispatch guard (`86bb4gy0y` punch-list item #3) targets the eval's other recurring
defect — leftover duplicate code after a `file_replace`: after a successful `file_replace` on a
`.py` path, `_find_duplicate_functions()` re-parses the file with `ast` and flags any two
functions sharing an identical normalized body, or the same name defined twice, returning the
same synthetic `is_error` corrective-nudge shape as the other three guards.

**Context-window pruning (`86bb4gy0y` punch-list item #2, 2026-07-29):** this endpoint's real
32,768-token context window (`CODING_AGENT_CONTEXT_WINDOW_TOKENS` — distinct from the file's
`NUM_CTX = 8192`, which is accepted for interface parity only and never actually forwarded to the
request) is a genuine constraint the 2026-07-27 held-out eval hit mid-task. Rather than detecting
an overflow after a remote failure (RunPod's exact error text for this case was never captured,
and `nova_remote_inference.chat()` — shared with the unrelated RAG path — returns bare `None` on
every failure type today, indistinguishable from a network error), `_prune_history_if_needed()`
proactively estimates the prompt's token size before every turn (a standard char-based
approximation, `CHARS_PER_TOKEN_ESTIMATE`) and drops the oldest complete
(assistant, tool-response) turn-pairs — never the system prompt or original task — until back
under budget, leaving an honest note on the earliest remaining pair. If even the single most
recent pair alone still overflows (one oversized tool call, unfixable by pruning), `run_via_runpod()`
stops with a new distinct `stopped_context_overflow` status **before** spending a paid RunPod
call on a request already known to fail — previously indistinguishable from a generic
`stopped_runpod_call_failed`. Unconditional once `runpod_coding_agent` is on, same as the other
four guards — no separate flag. Pruning events are visible in `agent_log.jsonl` via a new
`pruned_pairs` field.

**Real cost tracking (`86bb4gy0y` punch-list item #5, 2026-07-29):** RunPod bills per GPU-second
of real execution time, not per token — a real gap confirmed live: a completed job's response
includes top-level `executionTime`/`delayTime` fields (milliseconds) that
`nova_remote_inference._extract_answer()` previously discarded entirely, while
`nova_orchestrator_runpod.py`'s `_RunpodUsage` fed real RunPod token counts through
`nova_token_budget.record_usage()` — a budget model calibrated for Anthropic's per-token pricing,
producing a session/daily "budget %" with no relationship to real RunPod dollars spent. Fixed by
threading `execution_time_ms`/`delay_time_ms`/`cost_usd` through `chat()`'s return dict
(`RUNPOD_GPU_HOURLY_RATE_USD = 2.99`, this endpoint's real confirmed rate — **H100 SXM**, checked
directly against the RunPod dashboard, not assumed from a generic pricing page) and **removing**
`_RunpodUsage`/its `record_usage()` call entirely rather than keeping two disagreeing cost signals
side by side. Real per-task cost now lands in a new `logs/runpod_cost_log.jsonl`
(`_log_runpod_cost_summary()`), and per-turn cost is visible in `agent_log.jsonl` via a new
`cost_usd` field. Verified live: a real trivial task's three-turn run cost exactly $0.003591
total, matching the sum of its three logged per-turn costs. The separate
`budget_gate_enabled`/`get_budget_status().get("mode") == "halt"` check in `run_via_runpod()`'s
loop is untouched — it respects a shared, global halt state any caller (including the Claude
lane) may have already tripped, which stays valid even though this backend no longer writes into
that state itself.

**Coding review pass (`86bb4gy0y` punch-list item #1, 2026-07-29):** the review half of Marvin's
2026-07-27 review-split decision (RunPod/Qwen writes, Claude reviews — see
`project_coding_agent_review_split_decision.md`). `_review_coding_diff()` in
`nova_orchestrator.py` runs once, right after `run_coding_task()` computes the final diff and
before `_commit_worktree_changes()` — a single non-agentic `client.messages.create()` call
(same no-tool-use pattern as `nova_task_queue.propose_tier()`), never given a `tools` argument,
so it is structurally unable to write files regardless of what it says; its JSON verdict
(`approved`/`issues`/`summary`) only ever feeds a JSONL log entry and a commit-message string,
never back into a tool call. Gated behind `framework_integrations.coding_review_pass` (default
off, `aero_only`) **and** `runpod_coding_agent` together — meaningless without RunPod actually
having written the diff, so it never runs for the Claude-backed lane. Deliberately does **not**
block the commit or re-enter the turn loop on a negative verdict: a worktree commit here isn't a
merge (Marvin already reviews every diff by hand before that), so v1's job is to make that human
pass faster and start generating real (diff, verdict) pairs toward a future Qwen fine-tune
dataset (`logs/coding_review_log.jsonl`, written by `_log_coding_review()`), not to gate anything
itself yet.

### Escalation Protocol — Headless Dispatch (86bax0wkj, 2026-07-18)
A headless task dispatched via `nova_omen_dispatch.dispatch_headless_task()` can now
pause mid-task to ask Marvin a real question and resume once he answers, instead of
either guessing or running to completion with no way to get real-time input. Distinct
from `nova_task_queue.resolve_task_description()`'s existing "stop and say so plainly"
instruction: use that for blockers no answer would fix (missing credentials, an ask
outside scope); use the escalation block below only when a real answer from Marvin
would genuinely let the task continue.

**Block format** — must be the entire final message, with no further tool calls after
it (there is no way to "un-stop" a turn that kept going — `check_escalation()` only
ever runs after the SSH call has already returned):
```
NOVA_ESCALATION_START
QUESTION: <question text, may span multiple lines>
OPTIONS:
- <option 1>
- <option 2>
CONTEXT: <optional freeform context>
NOVA_ESCALATION_END
```
`OPTIONS` and `CONTEXT` are optional; `QUESTION` is required — its absence still
escalates (marked `malformed: true`, surfaced to Marvin rather than silently dropped).

**Mechanism, end to end:** `nova_escalation.check_escalation()` parses the block out of
the dispatch/resume result's own summary text via regex. `nova_scheduled_dispatch.py`'s
`_handle_escalation()` registers it with `nova_api.py`'s `POST /escalations`, tags
the ClickUp task `awaiting-answer`, and comments the question. Marvin answers via
`GET /escalations-ui` (redirects to `/controller`); the answer is accepted immediately
(fire-and-forget `BackgroundTasks`), and `nova_omen_dispatch.resume_headless_task()`
runs `claude -p --resume <session_id>` in the background, `cd`'d into the exact
original worktree. `POST /escalations/{id}/answer` requires header
`X-Nova-Escalation-Token` matching env var `NOVA_ESCALATION_TOKEN` — the first
cost-incurring write route on `nova_api.py`'s otherwise-unauthenticated Tailscale-only
surface. Resuming an escalated session is **not** blocked by the global dispatch-pause
switch (answering a direct question is a different act than starting a new autonomous
run). Full decision narrative in `NOVA_BUILD_LOG.md`.

**Manual step required:** Marvin must set `NOVA_ESCALATION_TOKEN` in the Omen's `.env`
and restart `nova-api` (or run `nova_omen_sync.py`) before the answer route will accept
anything — it 401s otherwise, by design (fail-closed, not a soft pass).

### Task Tiering (86bb01wur, 2026-07-19)
`nova_task_queue.propose_tier()` proposes an autonomy tier (`autonomous`/`needs review`/
`manual only`) + confidence + reasoning per task via a single non-agentic Claude completion.
Polling-based detection inside `nova_scheduled_dispatch.py`'s 2-hour loop (no ClickUp webhooks
exist). Reuses the escalation propose→register→notify→answer shape (`/tier-proposals`,
`X-Nova-Escalation-Token`). The `autonomous` tier maps to the existing `autonomy-safe` tag —
`get_practice_queue_tasks()` needed zero code change. `--sweep-tiers [--limit N]` CLI flag does
retroactive backlog backfill. Full build story (two real bugs found/fixed) in `NOVA_BUILD_LOG.md`.

### Nova Controller UX (86baxahn7, 2026-07-19)
One reverse-chronological Feed (`nova_controller.html`, `GET /controller`) replacing separate
dashboards — explicitly rejects engagement-optimization mechanics (no unread badges, no
streak pressure, strictly chronological, no ranking). Merges escalations, tier proposals,
dispatch outcomes (`GET /dispatch-log`), and tool-call/blend-flag/dpo-verify swipe-labeling
cards (`GET /label-queue`, `POST /label-queue/{kind}/{id}/decide`, token-gated). Hand-rolled
touch gestures, no library (no bundler in this repo's frontend). PWA-installable
(`manifest.json`/`sw.js`, app-shell-only caching, never live data). Optimistic UI + serialized
write queue for swipe/tap decisions (2026-07-26) — card collapses immediately, ~4s undo
snackbar, writes processed one at a time against the read-all/rewrite-all JSONL files.

**Token Budget Governor (86barhqt9):** `nova_token_budget.py` tracks the coding sub-agent's
Claude API consumption against `nova_config.json`'s `token_budget` thresholds
(`logs/token_budget_state.json`), classifies normal/conservative/critical/halt, folds into
`GET /headroom`. `nova_orchestrator.py` stops cleanly once halted. Gated behind
`token_budget_governor` (default off). Interactive lane only — headless dispatch doesn't call
`record_usage()`.

**Push Notifications — Layer 3 (86bb3ceyp, 2026-07-26):** `nova_notify.send_notification()`
POSTs to `https://ntfy.sh/<NTFY_TOPIC>` (ntfy.sh's public relay — no TLS/VAPID setup needed on
Nova's side, phone subscribes via the ntfy app). Wired into `nova_scheduled_dispatch.py`'s
`_post_non_clean_comment()` and `_handle_escalation()`, right after their existing `add_comment()`
calls — best-effort, never raises, silently no-ops if `push_notifications.enabled` is false or
`NTFY_TOPIC` is unset. **Security note:** ntfy.sh public topics are NOT access-controlled —
anyone who knows the topic string can publish/subscribe, and content transits ntfy's servers in
plaintext. `NTFY_TOPIC` must be a long random string, treated exactly like a secret, stored only
in `.env` on both machines. Generate one with:
```
python -c "import secrets; print('nova-' + secrets.token_urlsafe(24))"
```
then subscribe to the exact printed string in the ntfy phone app. One shared topic for every
notification type today (escalation vs. non-clean outcome distinguished only by title/tags, not
separate topics) — splitting into per-category topics is a plausible fast-follow, not built.

**Real-world gotcha found during setup, 2026-07-26:** `send_notification()` reliably reaches
ntfy.sh (confirmed via real POSTs — server-side delivery is not in question), and messages
correctly appear in the ntfy app's own history/Notification Center on Marvin's iPhone — but they
weren't confirmed showing as a live banner/alert with sound, even at `priority="urgent"`, after
walking through the most common iOS causes (Settings → Notifications → ntfy → Banners toggle,
Focus mode, the ntfy app's own per-topic instant-delivery setting). Deliberately not chased
further — checking the ntfy app periodically is an acceptable interim workflow, and this is an
iOS/ntfy-app configuration question, not a Nova code gap. Revisit if it becomes annoying enough
to be worth the troubleshooting time.

**Pre-Action Approval Gate (86bb3ceym, 2026-07-26):** `nova_orchestrator.py`'s `_execute_tool()`
can pause a `run_command` call — before it executes — when it matches a configured pattern
(`pre_action_approval_gate.command_patterns`, e.g. `pip install`, `git rebase`, `curl `), distinct
from `nova_tools.py`'s existing `DANGEROUS_COMMAND_PATTERNS` hard-block tier (which always
refuses and is untouched). `_request_tool_approval()` registers a `system/pending_tool_approvals`
record directly in `nova_state.db` (no HTTP hop — `/agent/task` already runs in-process on the
Aero's own `nova_api.py`), fires a push notification, then sleep-polls for a decision, failing
closed (denied) after `timeout_seconds` (default 300s). Marvin decides via a new Controller card
(`GET /tool-approvals`, `POST /tool-approvals/{id}/decide`, token-gated). Gated behind
`pre_action_approval_gate.enabled` (default off).

Scoped originally to the **Aero interactive lane** only (`POST /agent/task` →
`nova_orchestrator.py`) — since `/agent/task` and this gate both run on the Aero's own
`nova_api.py`, load the Controller from the **Aero's** address while a gated interactive task is
in flight, not the Omen's always-on `:8001` — the two processes have independent `nova_state.db`
files. `max_files_per_turn` file-count gating is designed (config field exists) but deliberately
not wired into `_approval_gate_reason()` yet — a fast-follow once the command-pattern trigger is
proven live.

**Headless-lane equivalent (86bb3r0h4, 2026-07-28):** the Omen headless dispatch lane
(`nova_omen_dispatch.py`'s `dispatch_headless_task()`, `dispatch_headless_task_sandboxed()`,
`resume_headless_task()`) runs the real `claude` CLI directly over SSH and never touches
`nova_tools.py`/`_execute_tool()` — the Aero-lane mechanism above literally cannot reach it. The
real fix is a genuine Claude Code `PreToolUse` hook (`nova_headless_approval_hook.py`, wired into
`.claude/settings.json`'s `PreToolUse`/`Bash` matcher, `timeout: 330`), not the
`NOVA_APPROVAL_START/END`-block idea originally floated on the ticket — verified live against
Claude Code's own hooks docs that a `PreToolUse` hook can synchronously `deny` a tool call via
stdout JSON (`{"hookSpecificOutput": {"permissionDecision": "deny", ...}}`), that this is enforced
**independently of permission-mode** (a `deny` still blocks under both `acceptEdits` and
`bypassPermissions`), and that headless `claude -p` honors project hooks identically to
interactive mode. Because `.claude/settings.json` is a tracked file, it — and the hook script
itself — ship into every dispatch worktree automatically via `git worktree add ... origin/master`,
no extra deploy step needed.

Scoped by `NOVA_HEADLESS_DISPATCH=1`, set only in `nova_omen_dispatch.py`'s three real headless
invocation sites — the hook is a silent no-op without it, so it never gates Marvin's own
interactive Claude Code sessions in this repo. On a match it registers via the new, unauthenticated
`POST /tool-approvals` (create) — a separate HTTP hop from the Aero lane's in-process
`nova_state.db` write, since the hook runs as its own subprocess — against the Omen's own
always-on `nova-api` over its **Tailscale IP**, not `127.0.0.1` (the sandboxed Docker path has no
`--network host`, so loopback wouldn't reach the host's nova-api there). Records are tagged
`lane: "interactive"` or `lane: "headless"` so the Controller's tool-approval card can show which
lane a pending approval came from; a self-reported `POST /tool-approvals/{id}/timeout` (also
unauthenticated — it's not a human decision, just the hook's own poll loop giving up) keeps a
timed-out headless record from showing as stuck-pending forever. Same shared
`pre_action_approval_gate.enabled` flag now gates both lanes at once — deliberately not split into
a second toggle for this first cut.

### Training-Data Accumulation Oversight (86bax4akx, 2026-07-21)
`GET /training-data-status` — live DPO pair count/coverage from `logs/training_flags.jsonl`
against `MIN_REAL_PAIRS_FOR_FINETUNE = 100`. New `dpo_verify` label kind on `/label-queue`
(unverified → confirmed-good/needs-rework). `nova_controller.html` progress-bar widget.
Cross-machine: `nova_training_data_status.py` gives `combined`/`omen_only`/`aero_only` views via
the Omen↔Aero SSH bridge (see below) since training data mostly lives on the Aero only. Full
incident history (a `/label-queue` truncation bug, hardcoded `C:/Nova` paths found in
`nova_logger.py`/`nova_corrector.py`, 17 more instances filed as `86bb1pkpb`) in
`NOVA_BUILD_LOG.md`.

### Nova Skills Library (2026-07-07, ClickUp `86barguac`)
Structured per-category instruction files (`skills/coding.md`, `retrieval.md`,
`financial.md`, `orchestration.md`, `lore.md`, `memory.md`) that `nova_orchestrator.py`
prepends to a coding task's context. `load_skill()`/`get_skill_version()` live in
`nova_skills.py`; category is an explicit caller-supplied string (not a ClickUp tag —
nothing in Nova's own runtime reads ClickUp). Gated behind `skill_injection` (default off).

### Domain State Layer (2026-07-07, ClickUp `86bara3qe`) — scoped v1
`nova_state.py` — one generic `domain_state` table (`domain`, `entity`, `data` JSON,
`updated_at`) per Architecture Principles v1.1 Principle 6 (Chroma = deep knowledge,
`nova_state.db` = current reality). `write_state`/`get_state`/`get_domain` are the only
interface. `nova_state.db` is local-only, gitignored. Only real adapter built:
`nova_state_system.py` (wraps `nova_headroom.get_headroom_report()` into
`system/nova_health`/`system/pending_alerts`). Financial/work/games/creative adapters
deferred, each blocked on a real open question (no approved data source, no ClickUp API
access from Nova's runtime, no art-practice-log data source). No alert engine, no refresh
scheduler yet.

**Known limitation, updated 2026-07-15 (`86baxbrmj` interim hardening):** the worktree
boundary is fully hard-enforced for `read_file`/`write_file`/`list_files` (path-validated
against `root` in `nova_tools.py`). `run_command` is a raw shell, so it is not equally
hard-enforced, but as of `86baxbrmj` it is no longer unrestricted either: it refuses any
`cd` that resolves outside the worktree root (`_cd_targets_outside_root`), and restricts
both `PATH` (`_build_restricted_path` — the live venv's Scripts dir, Git Bash's own bin
dirs, and the worktree root only, not the full inherited system PATH) and the subprocess
environment (`_build_restricted_env` — an explicit non-secret Windows/process-plumbing
allowlist, no longer a full `os.environ.copy()` carrying `.env` secrets like
`ANTHROPIC_API_KEY`/`CLICKUP_API_KEY` into every shell command). This closes the specific
failure previously observed here (a run cd'ing out looking for a Python interpreter and
falling back to the shared live venv) and the credential-exposure gap `86baxbrvv`'s audit
surfaced. **Remaining gap, deliberately accepted:** none of this stops an *allowed* binary
from taking an absolute-path argument — `git -C /c/Nova status` or `cat /c/Nova/.env` still
reach the live tree, since only `cd` targets are checked, not every argument to every
command. `nova_tools.py` also still has its best-effort denylist for obviously destructive
patterns (`rm -rf`, `git push`, `git reset --hard`, etc.) — a speed bump, not real
sandboxing, same as before. Accepted as reasonable for v1 — Claude is driving this, not an
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

### Known At-Risk Character Pairs (embedding-distance analysis, re-run 2026-07-16)
Real validation that `/embedding-viz` (Section 1/2, `86bawjg14`) actually predicts blending,
not just looks interesting: computed cosine distance between each character's chunk-embedding
centroid, ranked closest pairs, cross-referenced against real blend events in
`logs/training_flags.jsonl` (`sources_mixed` filtered to `CHARACTER_FILES`). **The two closest
pairs by embedding distance are exactly the two most frequent real blend pairs** — Helel↔Luci
(distance 0.124, closest overall, 4 real blend events) and Null↔Nullius (distance 0.134, 2nd
closest, **9 real events** — the single most common blend). Beat↔Rhythm (0.209, #3) and
Aseir↔Helel (0.275, #6) also each had a real event.

**Numbers re-run after the 2026-07-16 token-aware re-chunk (479→1989 chunks, Section 5 / change
log):** the correlation held cleanly — same top-2 pairs, same watch-list neighborhood — while all
distances tightened (Helel↔Luci 0.166→0.124, Null↔Nullius 0.193→0.134), as expected from cleaner
centroids once chunks were no longer truncated at embed time. This is real evidence the re-chunk
didn't disturb the character-cluster structure, only sharpened it.

**One honest exception found on re-run:** Aseir↔Beat has **2 real blend events** but is *not* among
the closest pairs by centroid distance — a tail case where embedding proximity under-predicts a
real blend. So the "closest ⇒ blends" correlation is strong for the top pairs, not perfect for
low-frequency ones.

**Watch list — close in embedding space, no blend event yet, but worth monitoring:** Aseir↔Luci
(0.256), Fatale Wildman↔Marisol (0.263), Helel↔Raven (0.289), Aseir↔Raven (0.298). Also newly
close on the re-chunk: Marisol↔Null (0.286), Fatale Wildman↔Null (0.287). If a future blend event
involves any of these, that's expected, not a new mystery — re-run the embedding-distance analysis
(the method above, or query `/embedding-viz` directly with `?refresh=1`) before assuming it's a
new/unrelated bug. Tracked as a watch item, not active work, in ClickUp `86bawnqdp`.

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
| /activity-profile | POST | ✓ Working | Merge one machine's Claude Code activity profile (hour-of-day/day-of-week histogram) into nova_state.db (system/claude_activity_profile) — called by nova_usage_logger.py's SessionEnd hook, alongside /usage-history |
| /activity-profile | GET | ✓ Working | Return the merged Claude Code activity profile across every machine that's pushed to it (no cross-machine summing yet) |
| /dispatch-pause | POST | ✓ Working | Set the headless-dispatch pause switch in nova_state.db (system/dispatch_pause) — called by nova_escalation.py's set_dispatch_pause(), always executes on the Omen's own copy regardless of caller's machine |
| /dispatch-pause | GET | ✓ Working | Return the current headless-dispatch pause state — called by nova_escalation.py's is_dispatch_paused() over HTTP instead of a direct nova_state.py import (2026-07-16 cross-machine fix) |
| /escalations | POST | ✓ Working | Register a new pending escalation (system/pending_escalations) — called by nova_scheduled_dispatch.py's _handle_escalation(), not token-gated (86bax0wkj) |
| /escalations | GET | ✓ Working | Return all escalations, pending and resolved — not token-gated (read-only) |
| /escalations/{id}/answer | POST | ✓ Working | Submit Marvin's answer — requires header X-Nova-Escalation-Token, fires a background resume via nova_omen_dispatch.resume_headless_task(), returns immediately |
| /escalations/{id}/cancel | POST | ✓ Working | Withdraw a pending escalation without answering it — token-gated. Distinct from /dispatch-abort: the dispatch behind an escalation has no live process left to kill (86bb3ceyj) |
| /escalations-ui | GET | ✓ Working | Redirects to /controller (86baxahn7) |
| /controller | GET | ✓ Working | Nova Controller Feed page (HTML, PWA-installable) — nova_controller.html |
| /dispatch-log | GET | ✓ Working | Merged dispatch/outcome history (scheduled_dispatch_log.jsonl + agent_task_outcomes.jsonl) |
| /in-flight-status | GET | ✓ Working | Is a headless dispatch running right now, and if so which task — backs the Controller's live status widget (86bb3cey0), headless-dispatch lane only. Also carries `fuel_source` as of 86bb3ceya (subscription/api_key) |
| /dispatch-cost-summary | GET | ✓ Working | Real (`api_key`-only) vs. notional (every run) headless-dispatch spend, today + last 7 days (86bb3ceya) |
| /qwen-swap-status | GET | ✓ Working | Progress toward Phase 3.5's Qwen3 8B swap trigger — full Aero+Omen `"combined"` count via live command-restricted SSH either direction, `"omen_only"`/`"aero_only"` only when the other machine genuinely can't be reached right now (e.g. asleep) (86bb3cey2) |
| /worktree-status | GET | ✓ Working | Open git worktrees on both machines with age/merged/prunable status — same live combined-or-partial `view` pattern as `/qwen-swap-status` (86bb3ceyc) |
| /flags | GET | ✓ Working | Current value of 9 important boolean flags (8 in nova_config.json + dispatch_pause) — backs the Controller's switches panel (86bb3d725) |
| /flags/{flag_key} | POST | ✓ Working | Toggle one flag — token-gated. Config-file flags commit locally on whichever machine handled the request but do NOT auto-push (the Omen's deploy key is read-only) |
| /label-queue | GET | ✓ Working | Unlabeled tool-call/blend-flag/dpo-verify entries awaiting a judge-pass — each kind capped at `limit` independently before merging (86bax4akx fix) |
| /label-queue/{kind}/{id}/decide | POST | ✓ Working | Patch a label decision — token-gated (X-Nova-Escalation-Token) |
| /training-data-status | GET | ✓ Working | Live DPO pair count, category coverage, verification status vs. the fine-tune floor (86bax4akx) |
| /tier-proposals | POST/GET | ✓ Working | Register/list pending autonomy-tier proposals (86bb01wur) |
| /tier-proposals/{id}/decide | POST | ✓ Working | Accept/override a tier proposal — token-gated |
| /tier-watermarks | GET/POST | ✓ Working | {task_id: last_seen date_updated} for tier-proposal creation/rescope detection |
| /dispatch-abort | POST | ✓ Working | Kill the currently-running cron-fired dispatch — token-gated. docker kill for sandboxed, direct SIGTERM/SIGKILL against the real captured PID for bare-SSH; worktree left in place for review (86bb3ceyj) |
| /worktree-pr | POST | ✓ Working | Push a dispatch branch + open a draft GitHub PR — token-gated. No custom diff viewer; Controller just deep-links to the real PR (86bb3ceyf) |
| /worktree-discard | POST | ✓ Working | Delete a dispatch worktree+branch outright — token-gated. Records the outcome via record_dispatch_review() when a task_id is given (86bb3ceyf) |
| /tool-approvals | POST | ✓ Working | Register a new pending tool-call approval (system/pending_tool_approvals) — called by nova_headless_approval_hook.py, not token-gated (86bb3r0h4) |
| /tool-approvals | GET | ✓ Working | Pending/decided tool-call approvals from the pre-action approval gate, both lanes (lane: "interactive"/"headless") — not token-gated (read-only) (86bb3ceym / 86bb3r0h4) |
| /tool-approvals/{id}/decide | POST | ✓ Working | Approve or deny a pending tool-call approval — token-gated. No background task; the originating lane's own poll loop picks up the decision |
| /tool-approvals/{id}/timeout | POST | ✓ Working | Self-reported timeout from nova_headless_approval_hook.py's poll loop — not token-gated (not a human decision); no-ops if already decided (86bb3r0h4) |

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
- **Standard git strategy (2026-07-14, until Nova's deployment story stabilizes):** once a
  commit is made and pushed to `origin/master`, run `nova_omen_sync.py` right after — don't
  leave the Omen's checkout to drift until someone notices. This is exactly the two-step gap
  (push, then a separate manual pull+restart on the Omen) that caused the 15-commit stale-clone
  incident documented in Section 2. `nova_omen_sync.py` still requires an explicit run each
  time (deliberately not a git hook, Marvin's call — see that script's own docstring) but
  should be treated as a normal trailing step of "push," not an optional extra. Revisit this
  once the Omen sync path is automated or replaced.
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

Full narrative detail for every entry below (the "why," bug stories, live-verification steps) lives
in `NOVA_BUILD_LOG.md` — this table is a terse date-ordered index, not the source of truth.

| Date       | Change                                        |
|------------|------------------------------------------------|
| 2026-06-15 | CLAUDE.md created; documented /context-budget bug |
| 2026-07-04 | Fixed /context-budget; Open WebUI OpenAI-compat routes; Nova Log v1 |
| 2026-07-05 | Phase 3.5 v1 coding sub-agent shipped; Phase 4 v1 (Tailscale/auto-start); golden benchmark suite |
| 2026-07-06 | `nova_mcp_server.py` (unwired); prompt caching; feature-flag system |
| 2026-07-10 | LangGraph orchestration v1 (gated off); model-routing + eval wrapper; Browser Hands M1 |
| 2026-07-11 | Chroma migrated to Omen-hosted `HttpClient`; `nova_board.py`/ClickUp CLI |
| 2026-07-12 | Omen headless server (`86baeyfm1`) COMPLETE; `nova_api.py` on Omen (`86bawfn19`) COMPLETE; chunk/embedding viz shipped |
| 2026-07-14 | `nova_omen_sync.py`; `nova_escalation.py` pause switch (stub); `nova_task_queue.py` |
| 2026-07-15 | `run_command` security hardening; Claude Code activity profile |
| 2026-07-16 | Dual-fuel credential switch; `nova_scheduled_dispatch.py` cron trigger; Qwen swap-trigger tracking; review-bandwidth backpressure |
| 2026-07-18 | Escalation Answer UI real (`check_escalation()` no longer a stub), `/escalations` routes |
| 2026-07-19 | Task Tiering; Nova Controller UX v1 (Feed, swipe-labeling, PWA); RunPod adapter; voice Minimal tier live-verified |
| 2026-07-21 | Phi-4 Mini fine-tune pipeline; base-model eval — verdict stay on Llama 3.2 3B; Omen capacity audit; training-data oversight (`/training-data-status`) |
| 2026-07-25 | Log rotation shipped; sandboxed-dispatch permission-mode bug fixed; 10 Controller-expansion tasks filed and 4 shipped same day (in-flight status, flags panel, cost summary, Qwen widget, Feed filtering, worktree browser, optimistic UI); Omen→Aero SSH bridge (3 read-only keys) built + activated |
| 2026-07-26 | Omen→Aero bridge extended to training-data read+write (4th key, write path built but not yet generated — stop-and-ask boundary); abort/kill switch (`86bb3ceyj`) and diff-preview-and-merge (`86bb3ceyf`) shipped, both Phase C Controller-expansion tasks |
| 2026-07-26 | CLAUDE.md split: narrative/incident history (~136K chars) moved to `NOVA_BUILD_LOG.md`, this file trimmed to current facts + standards |
| 2026-07-26 | Shipped the last two Controller-expansion tasks: push notifications (`86bb3ceyp`, `nova_notify.py`, ntfy.sh) and the pre-action approval gate (`86bb3ceym`, `nova_orchestrator.py`, Aero interactive lane only — headless-lane gap filed as `86bb3r0h4`). Verified live: real approve/deny/timeout poll-loop timing, full HTTP route auth/validation/state-transition behavior, and a real POST to ntfy.sh's live API. Both gated off by default; both flags toggleable from the Controller Switches panel. `NTFY_TOPIC` generated, set on both machines, `push_notifications.enabled` flipped on — messages confirmed reaching ntfy's history on Marvin's iPhone, but not yet as a live banner/alert (iOS notification settings, not a code gap — deferred, see Nova Controller UX subsection) |
| 2026-07-28 | Closed `86bb3r0h4` — headless-lane pre-action approval gate via a real Claude Code `PreToolUse` hook (`nova_headless_approval_hook.py`), not the `NOVA_APPROVAL_START/END`-block idea the ticket floated. Verified against Claude Code's own hooks docs that `PreToolUse` denials are enforced independently of permission-mode (works under both `acceptEdits` and `bypassPermissions`) and identically under headless `-p` mode. New `POST /tool-approvals` (create) and `POST /tool-approvals/{id}/timeout` routes, `NOVA_HEADLESS_DISPATCH=1` scoping env var on all three headless invocation sites, Controller lane badge. Same shared `pre_action_approval_gate.enabled` flag now covers both lanes. |
| 2026-07-29 | Merged the RunPod Qwen2.5-Coder-32B eval-spike branch (PR #16 — `nova_orchestrator_runpod.py`, `nova_coding_eval.py`, real 2/6-pass held-out result). Shipped `86bb4gy0y`'s punch-list items #1 and #3: a post-dispatch dead-code/leftover-duplicate guard (`_find_duplicate_functions()`, `ast`-based) closing the eval's other recurring defect, and the review-split's Claude-reviews-Qwen's-diff pass (`_review_coding_diff()`/`_log_coding_review()` in `nova_orchestrator.py`, new `coding_review_pass` flag). Review call verified structurally tool-less (no write path) per an explicit isolation guarantee confirmed with Marvin before building. Real end-to-end live test (both flags on, one trivial task) merged as `86bb4gy0y`'s first live proof, recorded via `record_task_outcome()`. |
| 2026-07-29 | Shipped `86bb4gy0y`'s punch-list item #2: proactive context-window pruning (`_prune_history_if_needed()` in `nova_orchestrator_runpod.py`) drops the oldest turn-pairs before each request to stay under this endpoint's real 32,768-token ceiling, instead of failing after the fact. New `stopped_context_overflow` status distinguishes an unavoidable single-turn overflow from a generic RunPod call failure. Verified via synthetic unit-style cases (no live API cost) covering under-budget no-op, real multi-pair pruning, and the unavoidable-single-pair-overflow signal. `nova_remote_inference.py` deliberately untouched (shared with the unrelated RAG path). |
| 2026-07-29 | Shipped `86bb4gy0y`'s punch-list item #5: real RunPod cost tracking. Confirmed live that a completed job's response carries `executionTime`/`delayTime` (RunPod's real GPU-second billing basis) that were previously discarded entirely, and that `_RunpodUsage` was feeding real token counts through a budget system calibrated for Anthropic pricing — removed that stand-in rather than keeping two disagreeing numbers. Real rate confirmed directly from the RunPod dashboard (H100 SXM, $2.99/hr). New `logs/runpod_cost_log.jsonl` per-task summary + `cost_usd` field in `agent_log.jsonl`. Verified live: a real 3-turn task's summary cost ($0.003591) exactly matched the sum of its per-turn logged costs. |
| 2026-07-29 | Re-ran the 6-task held-out eval after #1/#2/#3/#5 shipped — the dead-code guard did NOT close the gap (1/6 clean pass, 5/6 fail, including a real syntax error and a severe scope-violating rewrite that deleted the live RAG pipeline). Full result in memory `project_qwen3_coding_spike_result.md`. Marvin greenlit punch-list item #4 (real fine-tuning) in response. Shipped its data + pipeline half: `nova_coding_corrector.py` (new — Claude-written `chosen_diff` corrections, mirrors `nova_corrector.py`), `nova_finetune_qwen_coder.py` (new — QLoRA DPO pipeline mirroring `nova_finetune_phi4.py`, exports merged safetensors not GGUF since this model serves from an AWQ RunPod endpoint, not Ollama), and `nova_coding_eval.py` now seeds `coding_review_log.jsonl` on every run. Found and fixed a real bug in existing `_review_coding_diff()` (`max_tokens=600` too small once extended thinking eats the budget on a real diff — 5/6 backfilled reviews failed until raised to 4096). Backfilled real data from today's eval (free, no second RunPod spend): 6 real review verdicts, 5 real DPO pairs. Fine-tune script verified correct through model loading; confirmed to hit the expected hardware wall (32B checkpoint download) on the Aero, as documented — needs the still-unbuilt rented-A100 path (`86baf4e70`). |
| 2026-07-29 | Removed the "Nova Auto-Start" Task Scheduler task (`start_nova.ps1 -Silent` on Aero login) — Marvin no longer wants the Aero auto-starting `nova_api.py`/Open WebUI on login. `start_nova.ps1` itself untouched on disk for manual use. Also found and killed two stray `nova_api.py` dev instances (ports 8010/8011) left running since 2026-07-26, unrelated to any documented workflow. |
| 2026-07-31 | `86baf4e70` Pattern 1 v1: `nova_runpod_pod_launch.py` (new — launch/status/stop/terminate CLI for a rented RunPod A100 via raw REST, no new dependency) + `runpod_finetune_runbook.md` (new — manual steps once a pod is up). Deliberately provisioning-only, no auto-bootstrap of training, matching the task's `tier-manual-only` tag — a rented pod bills the moment it's RUNNING, so the spend decision stays a human running the CLI by hand. Real gotcha found before writing code: the finetune scripts' `from nova_orchestrator import CODING_REVIEW_LOG_PATH` transitively pulls in `anthropic`/`nova_state`/`nova_tools`/etc., so the pod needs a real `git clone` (via the Omen's existing read-only deploy key, reused rather than minting a new one), not a file copy. Endpoint paths (base URL, POST/GET/stop/DELETE) confirmed against docs.runpod.io live, not guessed. Not yet run against the real API — next real step is a live `launch` test. |

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
