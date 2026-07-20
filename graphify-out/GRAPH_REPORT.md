# Graph Report - Nova  (2026-07-19)

## Corpus Check
- 69 files · ~90,063 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 929 nodes · 1492 edges · 70 communities (58 shown, 12 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 26 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `170ecca1`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Coding Sub-Agent Orchestrator|Coding Sub-Agent Orchestrator]]
- [[_COMMUNITY_ClickUp Board CLI|ClickUp Board CLI]]
- [[_COMMUNITY_Wikilink Graph Builder|Wikilink Graph Builder]]
- [[_COMMUNITY_Embedding & Chunk Visualization|Embedding & Chunk Visualization]]
- [[_COMMUNITY_CLAUDE.md Architecture Doc|CLAUDE.md Architecture Doc]]
- [[_COMMUNITY_Headless Dispatch & Escalation|Headless Dispatch & Escalation]]
- [[_COMMUNITY_LangGraph Orchestration Port|LangGraph Orchestration Port]]
- [[_COMMUNITY_Board Status Digest & Token Budget|Board Status Digest & Token Budget]]
- [[_COMMUNITY_Browser Hands CDP Harness|Browser Hands CDP Harness]]
- [[_COMMUNITY_Resource Headroom Calculator|Resource Headroom Calculator]]
- [[_COMMUNITY_RAG Retrieval & Blend Logging|RAG Retrieval & Blend Logging]]
- [[_COMMUNITY_Claude Usage History Logger|Claude Usage History Logger]]
- [[_COMMUNITY_Omen Sync & Reachability Checks|Omen Sync & Reachability Checks]]
- [[_COMMUNITY_Golden Benchmark Suite|Golden Benchmark Suite]]
- [[_COMMUNITY_Nova MCP Server|Nova MCP Server]]
- [[_COMMUNITY_Nova API Core Routes|Nova API Core Routes]]
- [[_COMMUNITY_Feature Flag Config|Feature Flag Config]]
- [[_COMMUNITY_Nova API Request Models|Nova API Request Models]]
- [[_COMMUNITY_DPO Correction Generator|DPO Correction Generator]]
- [[_COMMUNITY_Browser Task State Writer|Browser Task State Writer]]
- [[_COMMUNITY_OpenAI-Compatible Chat Endpoint|OpenAI-Compatible Chat Endpoint]]
- [[_COMMUNITY_Nova Log Query View|Nova Log Query View]]
- [[_COMMUNITY_Nova Log Benchmark View|Nova Log Benchmark View]]
- [[_COMMUNITY_CLI Chat & Memory Store|CLI Chat & Memory Store]]
- [[_COMMUNITY_Graph Neighbors Endpoint|Graph Neighbors Endpoint]]
- [[_COMMUNITY_Coding Task Router Integration|Coding Task Router Integration]]
- [[_COMMUNITY_Browser Adapter Config Loader|Browser Adapter Config Loader]]
- [[_COMMUNITY_Tool-Call Logging & Hooks|Tool-Call Logging & Hooks]]
- [[_COMMUNITY_Query Category Router|Query Category Router]]
- [[_COMMUNITY_Omen Host Config Constants|Omen Host Config Constants]]
- [[_COMMUNITY_Launch Scripts|Launch Scripts]]
- [[_COMMUNITY_State Storage Tables|State Storage Tables]]
- [[_COMMUNITY_Browser Adapters Package|Browser Adapters Package]]
- [[_COMMUNITY_Browser Hands Package|Browser Hands Package]]
- [[_COMMUNITY_Chunk Viz ClickUp Ticket|Chunk Viz ClickUp Ticket]]
- [[_COMMUNITY_Browser Config Package|Browser Config Package]]
- [[_COMMUNITY_Browser Harness Package|Browser Harness Package]]
- [[_COMMUNITY_Ingest Ignore Patterns|Ingest Ignore Patterns]]
- [[_COMMUNITY_Ingest Supported Extensions|Ingest Supported Extensions]]
- [[_COMMUNITY_Status Snapshot File|Status Snapshot File]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]

## God Nodes (most connected - your core abstractions)
1. `CLAUDE.md — Nova Project Context & Coding Standards` - 35 edges
2. `ask()` - 23 edges
3. `run_coding_task()` - 21 edges
4. `get_state()` - 21 edges
5. `HP Omen Headless Ubuntu Server — Setup Runbook (v1.2)` - 20 edges
6. `write_state()` - 18 edges
7. `get_budget_status()` - 17 edges
8. `dispatch_headless_task()` - 15 edges
9. `run_scheduled_dispatch()` - 15 edges
10. `2. Architecture — Read Before Touching Anything` - 15 edges

## Surprising Connections (you probably didn't know these)
- `request_correction()` --semantically_similar_to--> `run_coding_task()`  [INFERRED] [semantically similar]
  nova_corrector.py → nova_orchestrator.py
- `sync_omen()` --semantically_similar_to--> `dispatch_headless_task()`  [INFERRED] [semantically similar]
  nova_omen_sync.py → nova_omen_dispatch.py
- `refresh_system_state()` --implements--> `Domain State Layer — scoped v1`  [EXTRACTED]
  nova_state_system.py → CLAUDE.md
- `refresh_system_state()` --references--> `nova_watcher.py — built, deferred (not running)`  [EXTRACTED]
  nova_state_system.py → CLAUDE.md
- `_categorize_board()` --semantically_similar_to--> `get_ready_tasks()`  [INFERRED] [semantically similar]
  nova_status_digest.py → nova_task_queue.py

## Import Cycles
- 1-file cycle: `nova_omen_dispatch.py -> nova_omen_dispatch.py`
- 1-file cycle: `nova_usage_logger.py -> nova_usage_logger.py`

## Hyperedges (group relationships)
- **Browser Hands harness M1 building blocks** — harness_cdp_connect_connect_to_chrome, harness_retry_safe_click, harness_selector_discovery_probe_selectors, harness_tree_walk_walk_virtualized_tree, harness_state_writer_record_run [EXTRACTED 1.00]
- **nova_board CLI command layer over nova_clickup_client** — nova_board_cmd_ready, nova_board_cmd_move, nova_board_cmd_block, nova_clickup_client_update_status [INFERRED 0.85]
- **Golden-query RAG benchmark flow** — nova_benchmark_run_golden_benchmark, nova_query_ask, nova_logger_detect_blending [INFERRED 0.85]
- **training_flags.jsonl blend-detection -> correction -> audit-overlay pipeline** — nova_logger_log_blend, nova_corrector_load_entries, nova_corrector_save_entries, nova_embedding_viz_dpo_corrected_filenames [INFERRED 0.95]
- **Headless dispatch pause-at-will control flow (nova_state.db-backed)** — nova_escalation_is_dispatch_paused, nova_escalation_set_dispatch_pause, nova_omen_dispatch_dispatch_headless_task, nova_state_write_state, nova_state_get_state [INFERRED 0.85]
- **Coding sub-agent turn loop: inline loop vs. LangGraph-ported implementation** — nova_orchestrator_run_coding_task, nova_orchestrator_graph_run_via_langgraph, nova_orchestrator_graph_build_graph [INFERRED 0.85]
- **Token Budget Governor v1 — tracking, gating, and documentation** — nova_token_budget_get_budget_status, nova_state_system_refresh_system_state, skills_orchestration_token_budget_gate, clickup_86barhqt9 [INFERRED 0.85]
- **86bax0exx headless dispatch pipeline — readiness, resolution, and tool-call visibility** — nova_task_queue_get_ready_tasks, nova_task_queue_resolve_task_description, nova_tool_call_log_log_tool_call, clickup_86bax0exx [EXTRACTED 1.00]
- **Nova Skills Library — six per-category skill files** — skills_coding_doc, skills_financial_doc, skills_lore_doc, skills_memory_doc, skills_orchestration_doc, skills_retrieval_doc [EXTRACTED 1.00]

## Communities (70 total, 12 thin omitted)

### Community 0 - "Coding Sub-Agent Orchestrator"
Cohesion: 0.22
Nodes (9): decide_label_queue_entry(), get_dispatch_log(), get_label_queue(), LabelDecisionRequest, Shared JSONL reader — same silently-skip-malformed-lines convention as nova_sche, Merged, sorted view of every real headless-dispatch outcome — backs     the Fee, Unlabeled tool-call and blend-flag entries awaiting a human judge-pass     — ba, Patch one tool_call_log.jsonl or training_flags.jsonl entry in place —     a re (+1 more)

### Community 1 - "ClickUp Board CLI"
Cohesion: 0.15
Nodes (25): ClickUp 86baf72n5 — ClickUp MCP tool-calling, build_parser(), cmd_audit(), cmd_check(), cmd_find(), cmd_help(), cmd_move(), cmd_ready() (+17 more)

### Community 2 - "Wikilink Graph Builder"
Cohesion: 0.06
Nodes (47): nova_watcher.py — built, deferred (not running), FileSystemEventHandler, build_graph(), _build_graph_from_chunks(), _fetch_all_chunks(), get_neighbors(), _load_graph(), _parse_links() (+39 more)

### Community 3 - "Embedding & Chunk Visualization"
Cohesion: 0.07
Nodes (37): ClickUp 86bawjg14 — Embedding-Space Visualization, ndarray, embedding_viz_data(), JSON data backing the /embedding-viz page — one point per Chroma chunk., _character_tag_for_chunk(), _detect_character(), FILENAME_TO_CHARACTER (reverse of CHARACTER_FILES), _format_chunk_block() (+29 more)

### Community 4 - "CLAUDE.md Architecture Doc"
Cohesion: 0.10
Nodes (21): 10. Change Log, 11. Session Startup Checklist, 4. Python Style Guide, 5. Known Issues & Active Bugs, 7. Nova API Routes, 8. What Claude Should Always Do, 9. What Claude Should Never Do, CLAUDE.md — Nova Project Context & Coding Standards (+13 more)

### Community 5 - "Headless Dispatch & Escalation"
Cohesion: 0.08
Nodes (29): create_tier_proposal(), get_activity_profile(), get_dispatch_pause_route(), get_escalations(), get_tier_proposals(), get_tier_watermarks(), get_usage_history(), Return the merged Claude Code usage history across every machine that has pushed (+21 more)

### Community 6 - "LangGraph Orchestration Port"
Cohesion: 0.18
Nodes (16): AgentTurnState, build_graph(), _call_model(), _check_budget(), _execute_tools(), Runs every tool_use block from the most recent assistant message and     appends, Deferred import so this module never needs nova_orchestrator.py at import time f, Wires the three nodes into the same shape as nova_orchestrator.py's     original (+8 more)

### Community 7 - "Board Status Digest & Token Budget"
Cohesion: 0.11
Nodes (21): Token Budget Governor — scoped v1, nova_tool_call_log.py — deliberately interim schema, ClickUp 86barby7t — Nova Log rotation (archive >90 days, keep last 1000), ClickUp 86bauwkvq — Token Budget Governor remaining scope (blocked), ClickUp 86bawntpb — Build tool-call logging schema for Nova subagents, ClickUp 86bawntpm — Draft Nova audit process (tool-call log review), ClickUp 86bawpvzz — Autonomous coding sessions initiative (not scoped), ClickUp 86bax697m — Adopt Langfuse as Nova's observability + auto-training-data backbone (+13 more)

### Community 8 - "Browser Hands CDP Harness"
Cohesion: 0.08
Nodes (31): Connection, connect_to_chrome(), _find_or_create_page(), Attach to an already-running Chrome instance over CDP and yield its page.      c, Return the first page whose URL contains url_hint, or a new page if none match., Click a locator with a bounded timeout. Returns True on success, False on     a, Read a locator's inner_text with a bounded timeout. Returns None on a     timeou, safe_click() (+23 more)

### Community 9 - "Resource Headroom Calculator"
Cohesion: 0.11
Nodes (26): _available_before_threshold(), build_headroom_summary(), compute_task_headroom(), _describe_budget_status(), _describe_pipeline_status(), _empty_gpu_stats(), get_active_session_count(), get_gpu_stats() (+18 more)

### Community 10 - "RAG Retrieval & Blend Logging"
Cohesion: 0.15
Nodes (22): ClickUp 86bawx7vj — bounded headless coding runner spec, ClickUp 86bax0exx — headless dispatch orchestration checklist, ClickUp 86bax0wkj — Nova Controller v1 (real escalation detection), check_escalation(), Parses a NOVA_ESCALATION_START/END block out of session_result's own     "summar, _build_credential_prefix(), choose_fuel_source(), dispatch_headless_task() (+14 more)

### Community 11 - "Claude Usage History Logger"
Cohesion: 0.09
Nodes (30): POST/GET /usage-history route, Usage-history centralization via SessionEnd hook, build_activity_profile(), build_daily_usage_history(), compute_entry_cost(), find_transcript_files(), iter_usage_entries(), normalize_model_id() (+22 more)

### Community 12 - "Omen Sync & Reachability Checks"
Cohesion: 0.14
Nodes (19): archive/test_nova.py — legacy Chroma/Ollama smoke test, CompletedProcess, main(), Raw socket check -- distinguishes 'nothing listening at all' from a Chroma-level, _tcp_reachable(), pull_latest(), Restart nova-api and nova-chroma via `sudo -n systemctl restart`, one     unit p, Poll a port until it accepts a connection or the timeout elapses. Returns True a (+11 more)

### Community 13 - "Golden Benchmark Suite"
Cohesion: 0.11
Nodes (27): get_context_budget(), Return a ranked list of filenames relevant to `query`.      Strategy:       1. Q, config_snapshot(), Flat snapshot of every flag's current value, for attaching to per-query     tele, log_query(), Append one real query's telemetry to query_log.jsonl.     Mirrors nova_logger.l, detect_blending(), log_blend() (+19 more)

### Community 14 - "Nova MCP Server"
Cohesion: 0.18
Nodes (14): Exception, nova_graph.json (wikilink graph nodes+edges), nova_context_budget(), nova_graph(), nova_ingest(), nova_neighbors(), nova_query(), _raise_for_request_failure() (+6 more)

### Community 15 - "Nova API Core Routes"
Cohesion: 0.07
Nodes (25): agent_task(), AgentTaskRequest, context_budget(), embedding_viz_page(), escalations_ui_redirect(), get_graph(), headroom(), list_models() (+17 more)

### Community 16 - "Feature Flag Config"
Cohesion: 0.22
Nodes (15): is_framework_integration_enabled(), True if the named framework integration flag is on. These are     independent fr, get_budget_status(), get_mode(), _load_state(), Classify the current mode from the finalized threshold table:     normal 0-50%,, Full current token-budget status: consumption, ceilings, mode, and     which par, Read the persisted token-budget state, or a fresh default if missing/corrupt. (+7 more)

### Community 17 - "Nova API Request Models"
Cohesion: 0.12
Nodes (16): BaseModel, ActivityProfilePushRequest, ask_nova(), AskRequest, DispatchPauseRequest, IngestRequest, push_activity_profile(), push_usage_history() (+8 more)

### Community 18 - "DPO Correction Generator"
Cohesion: 0.18
Nodes (15): nova_config.json (feature flag values), get_max_unreviewed_dispatches(), get_routed_model(), is_augment_enabled(), is_memory_decay_tier_enabled(), is_model_routing_enabled(), is_review_backpressure_enabled(), load_config() (+7 more)

### Community 19 - "Browser Task State Writer"
Cohesion: 0.09
Nodes (36): ClickUp 86barhqt9 — Token Budget Governor v1, _execute_tool(), Dispatch one Claude tool_use call to the matching nova_tools function.     Logs, _build_restricted_env(), _build_restricted_path(), _cd_targets_outside_root(), file_replace(), _find_second_brain_path() (+28 more)

### Community 20 - "OpenAI-Compatible Chat Endpoint"
Cohesion: 0.18
Nodes (11): _append_sources_footer(), _build_completion_response(), openai_chat_completions(), Split an OpenAI-style messages array into (query, history) for ask().      Dro, Append a markdown footer listing the retrieved source files, so every     Open, Build a non-streaming OpenAI chat.completion response body., Stream the answer as OpenAI-style server-sent events.      The answer is alrea, OpenAI-compatible chat endpoint for Open WebUI.      Runs the last user messag (+3 more)

### Community 21 - "Nova Log Query View"
Cohesion: 0.13
Nodes (19): nova_log_benchmarks(), nova_log_data(), nova_log_queries(), JSON data backing the /nova-log Health dashboard., Nova Log Query view — the last `limit` real queries (most recent first),     op, Nova Log Benchmark view — the last `limit` golden-query benchmark runs     from, BENCHMARK_LOG_PATH constant (nova_benchmark.py, not in this chunk), compute_health_summary() (+11 more)

### Community 22 - "Nova Log Benchmark View"
Cohesion: 0.09
Nodes (32): add_comment(), Post a comment on a task — used for automated status/outcome notifications (e.g., is_dispatch_paused(), Current pause state for headless dispatch, read from the Omen's own     nova-api, _acquire_lock(), handle_dispatch_outcome(), _handle_escalation(), _is_clean_outcome() (+24 more)

### Community 23 - "CLI Chat & Memory Store"
Cohesion: 0.10
Nodes (20): HP Omen Headless Server (86baeyfm1), HP Omen Headless Ubuntu Server — Setup Runbook (v1.2), PersistentClient -> HttpClient migration (discovered during setup), Not covered here — separate tracked decisions, Optional Appendix — Claude Code as Standby Maintenance Tool (unchanged from v1.1), ✓ Phase 0 — Before You Touch the Omen (done), ✓ Phase 10 — Firewall (ufw) (done — corrected in v1.2), ✓ Phase 11 — Tailscale on Ubuntu (done — 2026-07-12) (+12 more)

### Community 24 - "Graph Neighbors Endpoint"
Cohesion: 0.43
Nodes (6): chat(), _extract_answer(), _poll_until_terminal(), Send a chat completion request to Nova's RunPod-hosted model     (Qwen2.5-Coder-, Poll RunPod's /status/{id} endpoint until the job reaches a terminal     status, Parse a COMPLETED RunPod job's response into the same shape     ollama_client.ch

### Community 25 - "Coding Task Router Integration"
Cohesion: 0.67
Nodes (3): Any, Full overwrite, not merge-by-key -- the caller (detect_tier_candidates())     a, set_tier_watermarks()

### Community 26 - "Browser Adapter Config Loader"
Cohesion: 0.50
Nodes (3): Path, load_sites_config(), Read sites.yaml and return its parsed contents — one top-level key per     adapt

### Community 27 - "Tool-Call Logging & Hooks"
Cohesion: 0.67
Nodes (3): Claude Code settings.json hooks (SessionEnd/PostToolUse/PreToolUse), nova_tool_call_log.py (tool-call logging schema), nova_usage_logger.py (usage/cost history logger)

### Community 28 - "Query Category Router"
Cohesion: 0.08
Nodes (24): 2. Architecture — Read Before Touching Anything, Domain State Layer (2026-07-07, ClickUp `86bara3qe`) — scoped v1, Escalation Protocol — Headless Dispatch (86bax0wkj, 2026-07-18), File Locations, HP Omen Headless Server (ClickUp `86baeyfm1`) — ✓ COMPLETE (2026-07-12), Important gap in the "COMPLETE" verification above, found and fixed same day (`86bawfn19`), Key External Dependencies, Nova Coding Sub-Agent (nova_orchestrator.py) (+16 more)

### Community 29 - "Omen Host Config Constants"
Cohesion: 1.00
Nodes (3): graph_builder.py CHROMA_HOST constant (192.168.1.250), ingest.py CHROMA_HOST constant (192.168.1.250), OMEN_HOST constant (192.168.1.250)

### Community 45 - "Community 45"
Cohesion: 0.22
Nodes (8): background_color, display, icons, name, scope, short_name, start_url, theme_color

### Community 46 - "Community 46"
Cohesion: 0.27
Nodes (10): get_combined_status(), _parse_jsonl(), Group a list of agent_log.jsonl entries by task_slug, returning     {task_slug:, Merge the Aero's local agent_log.jsonl with the Omen's fetched-over-SSH     copy, Parse a JSONL blob into a list of dicts, silently skipping blank/malformed lines, Read this machine's own logs/agent_log.jsonl. Empty list if it doesn't exist yet, Fetch the Omen's logs/agent_log.jsonl over SSH — same host/user/path     nova_om, read_local_agent_log() (+2 more)

### Community 47 - "Community 47"
Cohesion: 0.10
Nodes (29): blend(), _convert_agentinstruct(), convert_all(), _convert_apibench(), _convert_finqa(), _convert_tatqa(), _convert_toolbench(), _dataset_raw_dir() (+21 more)

### Community 48 - "Community 48"
Cohesion: 0.12
Nodes (24): ClickUp 86barguac — Nova Skills Library, _build_system_prompt(), _commit_worktree_changes(), _create_worktree(), _git_diff_against_master(), _log_agent_turn(), Path, Turn a task description into a short, unique, filesystem-safe slug. (+16 more)

### Community 49 - "Community 49"
Cohesion: 0.20
Nodes (9): Financial data never enters Chroma (hard boundary), Constraints, Conventions, Examples, Nova Skill: Memory, Output format, Purpose, Chroma vs. nova_state.db routing rule (+1 more)

### Community 50 - "Community 50"
Cohesion: 0.20
Nodes (9): Fiction/lore retrieval capped at 3 chunks, Chunk limits — general 6, fiction/lore 3, Constraints, Conventions, Examples, Flat vector search default; A* graph traversal only for multi-hop queries, Nova Skill: Retrieval, Output format (+1 more)

### Community 51 - "Community 51"
Cohesion: 0.22
Nodes (8): Known At-Risk Character Pairs (86bawnqdp), Character Blending Fixes, Constraints, Conventions, Examples, Nova Skill: Lore, Output format, Purpose

### Community 52 - "Community 52"
Cohesion: 0.22
Nodes (8): Nova Skills Library, Constraints, Conventions, Examples, Money as INTEGER cents convention, Nova Skill: Financial, Output format, Purpose

### Community 53 - "Community 53"
Cohesion: 0.29
Nodes (6): 1. What Nova's chunking does today, 2. What Chonkie is (⚠️ version/dep specifics unverified — web was blocked), 3. Fit for Nova, 4. Recommendation, Chonkie Evaluation (ingest.py chunking), Evaluate Chonkie for `ingest.py` chunking

### Community 54 - "Community 54"
Cohesion: 0.22
Nodes (9): BackgroundTasks, answer_escalation(), _check_escalation_token(), decide_tier_proposal(), EscalationAnswerRequest, Fail-closed token check for the one cost-incurring write route on this     othe, Accept Marvin's answer immediately (fire-and-forget) and resume the     exact s, Accept or override a pending tier proposal. Token-gated, reusing the     same X (+1 more)

### Community 55 - "Community 55"
Cohesion: 0.33
Nodes (5): Blocked (12), Changed since last digest, In progress (4), Nova Board Status Digest, Ready (89)

### Community 56 - "Community 56"
Cohesion: 0.40
Nodes (5): 6. RAG Architecture — Critical Details, Character Blending Fixes (already applied — do not revert), Character Query Handling, Graph-Guided Retrieval Flow (retrieve_with_graph), Known At-Risk Character Pairs (embedding-distance analysis, re-run 2026-07-16)

### Community 57 - "Community 57"
Cohesion: 0.67
Nodes (3): 1. Project Overview, Current Phase: Phase 1 — Memory Core (active), Phase Roadmap

### Community 58 - "Community 58"
Cohesion: 0.67
Nodes (3): 3. Coding Philosophy — Legibility First, Comment Style, Core Rules

### Community 60 - "Community 60"
Cohesion: 0.67
Nodes (3): create_escalation(), EscalationCreateRequest, Register a new pending escalation — called by     nova_scheduled_dispatch.py's

### Community 62 - "Community 62"
Cohesion: 0.12
Nodes (24): cmd_block(), cmd_link(), cmd_split(), cmd_unlink(), add_dependency(), add_tag(), _api_key(), create_task() (+16 more)

### Community 63 - "Community 63"
Cohesion: 0.12
Nodes (20): nova_task_queue.py scope decisions (Drive doc vs. description; manual-trigger only), ClickUp 86bax0exx — Nova orchestration layer: task-queue -> headless run -> review loop, _current_tier_tag(), _description_hash(), detect_tier_candidates(), _fetch_tier_watermarks(), get_ready_tasks(), _is_tierable() (+12 more)

### Community 64 - "Community 64"
Cohesion: 0.16
Nodes (17): _aggregate_golden_results(), benchmark_log.jsonl (golden benchmark run log), evaluate_candidate(), _get_latest_baseline_entry(), _log_golden_benchmark(), Run one golden query through the full RAG pipeline (nova_query.ask),     timing, Roll up per-query golden benchmark results into the summary stats     written t, Append one JSON entry to benchmark_log.jsonl. Mirrors nova_log.py's     append (+9 more)

### Community 65 - "Community 65"
Cohesion: 0.31
Nodes (10): Anthropic, find_character_file(), load_entries(), load_lore(), Search the Second Brain for a file matching filename., Load and concatenate lore content for each source file., Ask Claude to write an accurate response grounded only in the lore provided., request_correction() (+2 more)

### Community 66 - "Community 66"
Cohesion: 0.48
Nodes (5): main(), clear_history(), load_history(), Load conversation history from disk. Returns empty list if none exists., Delete the history file.

### Community 67 - "Community 67"
Cohesion: 0.29
Nodes (7): _extract_changed_files(), format_coding_task_summary(), handle_coding_task(), Pull the list of changed filenames out of a unified git diff, instead of     sc, Build a plain-text summary of a nova_orchestrator.run_coding_task() result, Strip the CODING_AGENT_PREFIX off the original (case-preserved) query,     hand, CODING_AGENT_PREFIX constant ('/code ')

### Community 68 - "Community 68"
Cohesion: 0.29
Nodes (7): count_unreviewed_dispatches(), _pending_escalation_task_ids(), Path, Read a JSONL file into a list of dicts, silently skipping blank/malformed lines., task_ids currently mid-escalation (status "pending" or "resuming" in     nova_ap, Unreviewed = a scheduled_dispatch_log.jsonl entry with a real     session_id (a, _read_jsonl()

### Community 69 - "Community 69"
Cohesion: 0.50
Nodes (4): get_dependency_chain(), _own_blockers(), Filters a task's raw `dependencies` field down to entries where this     task is, Walks this task's own blockers recursively, stopping at a blocker that's     com

## Ambiguous Edges - Review These
- `nova_graph()` → `nova_graph.json (wikilink graph nodes+edges)`  [AMBIGUOUS]
  nova_mcp_server.py · relation: references

## Knowledge Gaps
- **148 isolated node(s):** `Path`, `Connection`, `name`, `short_name`, `start_url` (+143 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `nova_graph()` and `nova_graph.json (wikilink graph nodes+edges)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `CLAUDE.md — Nova Project Context & Coding Standards` connect `CLAUDE.md Architecture Doc` to `Wikilink Graph Builder`, `Board Status Digest & Token Budget`, `Claude Usage History Logger`, `Community 51`, `Community 52`, `Browser Task State Writer`, `CLI Chat & Memory Store`, `Community 56`, `Community 57`, `Community 58`, `Community 59`, `Query Category Router`?**
  _High betweenness centrality (0.219) - this node is a cross-community bridge._
- **Why does `run_command()` connect `Browser Task State Writer` to `Community 48`, `CLAUDE.md Architecture Doc`, `Query Category Router`?**
  _High betweenness centrality (0.099) - this node is a cross-community bridge._
- **Why does `file_replace()` connect `Browser Task State Writer` to `Community 48`, `CLAUDE.md Architecture Doc`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **What connects `Path`, `Read sites.yaml and return its parsed contents — one top-level key per     adapt`, `Attach to an already-running Chrome instance over CDP and yield its page.      c` to the rest of the system?**
  _440 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Wikilink Graph Builder` be split into smaller, more focused modules?**
  _Cohesion score 0.05909090909090909 - nodes in this community are weakly interconnected._
- **Should `Embedding & Chunk Visualization` be split into smaller, more focused modules?**
  _Cohesion score 0.0728744939271255 - nodes in this community are weakly interconnected._