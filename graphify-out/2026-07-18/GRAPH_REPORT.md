# Graph Report - .  (2026-07-14)

## Corpus Check
- 66 files · ~62,120 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 622 nodes · 1019 edges · 45 communities (35 shown, 10 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 26 edges (avg confidence: 0.87)
- Token cost: 504,706 input · 0 output

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

## God Nodes (most connected - your core abstractions)
1. `ask()` - 22 edges
2. `CLAUDE.md — Nova Project Context & Coding Standards` - 22 edges
3. `run_coding_task()` - 21 edges
4. `get_budget_status()` - 17 edges
5. `get_headroom_report()` - 14 edges
6. `get_task()` - 11 edges
7. `list_board_tasks()` - 11 edges
8. `get_unresolved_blockers()` - 11 edges
9. `load_config()` - 11 edges
10. `rebuild_node()` - 10 edges

## Surprising Connections (you probably didn't know these)
- `request_correction()` --semantically_similar_to--> `run_coding_task()`  [INFERRED] [semantically similar]
  nova_corrector.py → nova_orchestrator.py
- `sync_omen()` --semantically_similar_to--> `dispatch_headless_task()`  [INFERRED] [semantically similar]
  nova_omen_sync.py → nova_omen_dispatch.py
- `refresh_system_state()` --references--> `nova_watcher.py — built, deferred (not running)`  [EXTRACTED]
  nova_state_system.py → CLAUDE.md
- `Every spawned task passes the token budget gate before starting` --references--> `get_budget_status()`  [EXTRACTED]
  skills/orchestration.md → nova_token_budget.py
- `Atomic per-change commit convention ([module] short imperative)` --conceptually_related_to--> `file_replace()`  [INFERRED]
  skills/coding.md → nova_tools.py

## Import Cycles
- None detected.

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

## Communities (45 total, 10 thin omitted)

### Community 0 - "Coding Sub-Agent Orchestrator"
Cohesion: 0.07
Nodes (49): Nova Coding Sub-Agent (nova_orchestrator.py), ClickUp 86barhqt9 — Token Budget Governor v1, ClickUp 86barguac — Nova Skills Library, _build_system_prompt(), _commit_worktree_changes(), _create_worktree(), _execute_tool(), _git_diff_against_master() (+41 more)

### Community 1 - "ClickUp Board CLI"
Cohesion: 0.08
Nodes (49): ClickUp 86baf72n5 — ClickUp MCP tool-calling, build_parser(), cmd_audit(), cmd_block(), cmd_check(), cmd_find(), cmd_help(), cmd_link() (+41 more)

### Community 2 - "Wikilink Graph Builder"
Cohesion: 0.07
Nodes (41): nova_watcher.py — built, deferred (not running), FileSystemEventHandler, build_graph(), _build_graph_from_chunks(), _fetch_all_chunks(), _load_graph(), _parse_links(), Load nova_graph.json from disk; return empty graph if missing. (+33 more)

### Community 3 - "Embedding & Chunk Visualization"
Cohesion: 0.07
Nodes (37): ClickUp 86bawjg14 — Embedding-Space Visualization, ndarray, embedding_viz_data(), JSON data backing the /embedding-viz page — one point per Chroma chunk., _character_tag_for_chunk(), _detect_character(), FILENAME_TO_CHARACTER (reverse of CHARACTER_FILES), _format_chunk_block() (+29 more)

### Community 4 - "CLAUDE.md Architecture Doc"
Cohesion: 0.07
Nodes (35): Known At-Risk Character Pairs (86bawnqdp), Character Blending Fixes, nova_escalation.py — escalation hook + pause-at-will, The Golden Rule — FastAPI is the only interface, HP Omen Headless Server (86baeyfm1), Nova Skills Library, CLAUDE.md — Nova Project Context & Coding Standards, Session Startup Checklist (+27 more)

### Community 5 - "Headless Dispatch & Escalation"
Cohesion: 0.09
Nodes (28): Domain State Layer — scoped v1, ClickUp 86bawx7vj — bounded headless coding runner spec, ClickUp 86bax0exx — headless dispatch orchestration checklist, ClickUp 86bax0wkj — Nova Controller v1 (real escalation detection), get_usage_history(), Return the merged Claude Code usage history across every machine that has pushed, check_escalation(), is_dispatch_paused() (+20 more)

### Community 6 - "LangGraph Orchestration Port"
Cohesion: 0.10
Nodes (31): is_framework_integration_enabled(), True if the named framework integration flag is on. These are     independent fr, AgentTurnState, build_graph(), _call_model(), _check_budget(), _execute_tools(), Runs every tool_use block from the most recent assistant message and     appends (+23 more)

### Community 7 - "Board Status Digest & Token Budget"
Cohesion: 0.10
Nodes (26): nova_task_queue.py scope decisions (Drive doc vs. description; manual-trigger only), Token Budget Governor — scoped v1, nova_tool_call_log.py — deliberately interim schema, ClickUp 86bauwkvq — Token Budget Governor remaining scope (blocked), ClickUp 86bawntpb — Build tool-call logging schema for Nova subagents, ClickUp 86bawntpm — Draft Nova audit process (tool-call log review), ClickUp 86bawpvzz — Autonomous coding sessions initiative (not scoped), ClickUp 86bax0exx — Nova orchestration layer: task-queue -> headless run -> review loop (+18 more)

### Community 8 - "Browser Hands CDP Harness"
Cohesion: 0.10
Nodes (22): base44_export.py reference script (proven CDP-attach patterns), connect_to_chrome(), _find_or_create_page(), Attach to an already-running Chrome instance over CDP and yield its page.      c, Return the first page whose URL contains url_hint, or a new page if none match., Click a locator with a bounded timeout. Returns True on success, False on     a, Read a locator's inner_text with a bounded timeout. Returns None on a     timeou, safe_click() (+14 more)

### Community 9 - "Resource Headroom Calculator"
Cohesion: 0.11
Nodes (26): headroom(), Return Nova's current resource headroom report — VRAM (nvidia-smi), RAM     + C, _available_before_threshold(), build_headroom_summary(), compute_task_headroom(), _describe_budget_status(), _describe_pipeline_status(), _empty_gpu_stats() (+18 more)

### Community 10 - "RAG Retrieval & Blend Logging"
Cohesion: 0.15
Nodes (20): get_context_budget(), Return a ranked list of filenames relevant to `query`.      Strategy:       1. Q, log_query(), Append one real query's telemetry to query_log.jsonl.     Mirrors nova_logger.l, detect_blending(), log_blend(), Return True when a fiction query pulled chunks from more than one     character, Write one flagged exchange to both JSONL and markdown. (+12 more)

### Community 11 - "Claude Usage History Logger"
Cohesion: 0.13
Nodes (19): POST/GET /usage-history route, Usage-history centralization via SessionEnd hook, build_daily_usage_history(), compute_entry_cost(), find_transcript_files(), iter_usage_entries(), normalize_model_id(), push_daily_usage_history() (+11 more)

### Community 12 - "Omen Sync & Reachability Checks"
Cohesion: 0.15
Nodes (17): archive/test_nova.py — legacy Chroma/Ollama smoke test, CompletedProcess, main(), Raw socket check -- distinguishes 'nothing listening at all' from a Chroma-level, _tcp_reachable(), pull_latest(), Restart nova-api and nova-chroma via `sudo -n systemctl restart`, one     unit p, Confirm both services are accepting TCP connections again after the     restart. (+9 more)

### Community 13 - "Golden Benchmark Suite"
Cohesion: 0.16
Nodes (17): _aggregate_golden_results(), benchmark_log.jsonl (golden benchmark run log), evaluate_candidate(), _get_latest_baseline_entry(), _log_golden_benchmark(), Run one golden query through the full RAG pipeline (nova_query.ask),     timing, Roll up per-query golden benchmark results into the summary stats     written t, Append one JSON entry to benchmark_log.jsonl. Mirrors nova_log.py's     append (+9 more)

### Community 14 - "Nova MCP Server"
Cohesion: 0.18
Nodes (14): Exception, nova_graph.json (wikilink graph nodes+edges), nova_context_budget(), nova_graph(), nova_ingest(), nova_neighbors(), nova_query(), _raise_for_request_failure() (+6 more)

### Community 15 - "Nova API Core Routes"
Cohesion: 0.14
Nodes (13): context_budget(), embedding_viz_page(), list_models(), nova_log_page(), push_usage_history(), Return a ranked list of filenames most relevant to `query`,     combining seman, OpenAI-compatible model list. Open WebUI calls this to fill its model picker., Merge one machine's locally-computed daily Claude Code usage history into     n (+5 more)

### Community 16 - "Feature Flag Config"
Cohesion: 0.21
Nodes (13): config_snapshot(), nova_config.json (feature flag values), get_routed_model(), is_augment_enabled(), is_memory_decay_tier_enabled(), is_model_routing_enabled(), load_config(), True if per-category model routing is on. Independent flag, no shared master swi (+5 more)

### Community 17 - "Nova API Request Models"
Cohesion: 0.15
Nodes (13): BaseModel, agent_task(), AgentTaskRequest, ask_nova(), AskRequest, IngestRequest, Full RAG pipeline.     Returns answer, sources, category, and chunk metadata., Trigger incremental (default) or full re-ingest. (+5 more)

### Community 18 - "DPO Correction Generator"
Cohesion: 0.31
Nodes (10): Anthropic, find_character_file(), load_entries(), load_lore(), Search the Second Brain for a file matching filename., Load and concatenate lore content for each source file., Ask Claude to write an accurate response grounded only in the lore provided., request_correction() (+2 more)

### Community 19 - "Browser Task State Writer"
Cohesion: 0.24
Nodes (10): Connection, _get_connection(), get_recent_runs(), One adapter run's outcome — matches the Build Spec's Section 2.2 adapter     con, Open a connection to nova_state.db, creating the browser_tasks table if needed., Write one RunResult as a new browser_tasks row. `captured_at` is     generated h, Read back the most recent browser_tasks rows, newest first. If `adapter`     is, record_run() (+2 more)

### Community 20 - "OpenAI-Compatible Chat Endpoint"
Cohesion: 0.18
Nodes (11): _append_sources_footer(), _build_completion_response(), openai_chat_completions(), Split an OpenAI-style messages array into (query, history) for ask().      Dro, Append a markdown footer listing the retrieved source files, so every     Open, Build a non-streaming OpenAI chat.completion response body., Stream the answer as OpenAI-style server-sent events.      The answer is alrea, OpenAI-compatible chat endpoint for Open WebUI.      Runs the last user messag (+3 more)

### Community 21 - "Nova Log Query View"
Cohesion: 0.24
Nodes (10): nova_log_queries(), Nova Log Query view — the last `limit` real queries (most recent first),     op, compute_health_summary(), _entry_matches_filters(), get_recent_queries(), Real, currently-available Health stats computed from query_log.jsonl:     total, Check a single query_log.jsonl entry against the optional filters below., Return the most recent query_log.jsonl entries, most-recent-first.      Backs (+2 more)

### Community 22 - "Nova Log Benchmark View"
Cohesion: 0.22
Nodes (9): nova_log_benchmarks(), nova_log_data(), JSON data backing the /nova-log Health dashboard., Nova Log Benchmark view — the last `limit` golden-query benchmark runs     from, BENCHMARK_LOG_PATH constant (nova_benchmark.py, not in this chunk), get_benchmark_runs(), Return the most recent benchmark_log.jsonl entries, most-recent-first.      Ba, Read every line of benchmark_log.jsonl into dicts. Empty list if the file     d (+1 more)

### Community 23 - "CLI Chat & Memory Store"
Cohesion: 0.33
Nodes (7): main(), clear_history(), load_history(), Load conversation history from disk. Returns empty list if none exists., Save conversation history to disk, keeping only the last MAX_EXCHANGES exchanges, Delete the history file., save_history()

### Community 24 - "Graph Neighbors Endpoint"
Cohesion: 0.29
Nodes (7): get_neighbors(), Return outgoing and incoming edges for `filename`.      {       "file": filename, get_graph(), _load_graph_json(), neighbors(), Return the full node/edge map from nova_graph.json., Return all outgoing and incoming edges for a given file.

### Community 25 - "Coding Task Router Integration"
Cohesion: 0.29
Nodes (7): _extract_changed_files(), format_coding_task_summary(), handle_coding_task(), Pull the list of changed filenames out of a unified git diff, instead of     sc, Build a plain-text summary of a nova_orchestrator.run_coding_task() result, Strip the CODING_AGENT_PREFIX off the original (case-preserved) query,     hand, CODING_AGENT_PREFIX constant ('/code ')

### Community 26 - "Browser Adapter Config Loader"
Cohesion: 0.50
Nodes (3): Path, load_sites_config(), Read sites.yaml and return its parsed contents — one top-level key per     adapt

### Community 27 - "Tool-Call Logging & Hooks"
Cohesion: 0.50
Nodes (4): Claude Code settings.json hooks (SessionEnd/PostToolUse/PreToolUse), graphify-out/graph.json (knowledge graph output), nova_tool_call_log.py (tool-call logging schema), nova_usage_logger.py (usage/cost history logger)

### Community 28 - "Query Category Router"
Cohesion: 0.67
Nodes (3): Classify a query and return a RouteResult with retrieval hints.     Fast keywor, route(), RouteResult

### Community 29 - "Omen Host Config Constants"
Cohesion: 1.00
Nodes (3): graph_builder.py CHROMA_HOST constant (192.168.1.250), ingest.py CHROMA_HOST constant (192.168.1.250), OMEN_HOST constant (192.168.1.250)

## Ambiguous Edges - Review These
- `nova_graph()` → `nova_graph.json (wikilink graph nodes+edges)`  [AMBIGUOUS]
  nova_mcp_server.py · relation: references

## Knowledge Gaps
- **56 isolated node(s):** `Path`, `Connection`, `StreamingResponse`, `ndarray`, `Exception` (+51 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `nova_graph()` and `nova_graph.json (wikilink graph nodes+edges)`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `CLAUDE.md — Nova Project Context & Coding Standards` connect `CLAUDE.md Architecture Doc` to `Coding Sub-Agent Orchestrator`, `Wikilink Graph Builder`, `Headless Dispatch & Escalation`, `Board Status Digest & Token Budget`, `Claude Usage History Logger`?**
  _High betweenness centrality (0.158) - this node is a cross-community bridge._
- **Why does `log_tool_call()` connect `Board Status Digest & Token Budget` to `Coding Sub-Agent Orchestrator`?**
  _High betweenness centrality (0.085) - this node is a cross-community bridge._
- **Why does `record_run()` connect `Browser Task State Writer` to `Browser Hands CDP Harness`?**
  _High betweenness centrality (0.083) - this node is a cross-community bridge._
- **What connects `Path`, `Read sites.yaml and return its parsed contents — one top-level key per     adapt`, `Attach to an already-running Chrome instance over CDP and yield its page.      c` to the rest of the system?**
  _265 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Coding Sub-Agent Orchestrator` be split into smaller, more focused modules?**
  _Cohesion score 0.06636500754147813 - nodes in this community are weakly interconnected._
- **Should `ClickUp Board CLI` be split into smaller, more focused modules?**
  _Cohesion score 0.08078431372549019 - nodes in this community are weakly interconnected._