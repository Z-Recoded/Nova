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
  query's telemetry in `query_log.jsonl` so flag state is tied to results before any augment exists.
  **Hardcoded-Windows-path bug fixed 2026-07-16** (same class as the `nova_orchestrator.py` dotenv
  and `nova_api.py` `GRAPH_PATH` incidents): `CONFIG_PATH` was `"C:/Nova/nova_config.json"` — on the
  Omen (Linux, runs `nova_scheduled_dispatch.py` natively via cron), `load_config()` silently fell
  back to `DEFAULT_CONFIG` instead of reading the real, already-present file, with zero error signal.
  Confirmed live via SSH before fixing. Now resolved relative to the script's own location. Found
  while building the review-backpressure cap below — would have shipped a gate that's silently
  always off exactly where it needs to run
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
  list. Push target defaults to the Omen's Tailscale address (`NOVA_API_URL`,
  `http://100.114.197.117:8001`) — the real cross-machine centralization target from any
  machine, including the Omen itself. **Activity profile (2026-07-15, `86bawpvzz`
  groundwork):** the same transcripts also feed `build_activity_profile()`, a second
  derived artifact — an hour-of-day/day-of-week message-count histogram, windowed to the
  last 60 days (recent-schedule signal, not diluted by stale history), written to
  `logs/claude_activity_profile.json` and pushed via the same `--push` flag to `nova_api.py`'s
  new `POST /activity-profile`, merging into `nova_state.db`'s `system/claude_activity_profile`
  entity. Exists to find genuine "Marvin is away from Claude Code" windows for the planned
  autonomous-dispatch dual-fuel design (subscription auth by default, fall back to a funded
  metered key once usage headroom gets low) — a real activity histogram instead of a guessed
  reserve percentage. Deliberately Claude Code only: claude.ai chat activity timing isn't
  obtainable on a personal (non-Enterprise) plan, checked directly against Anthropic's
  Usage/Cost and Enterprise Analytics API docs before assuming otherwise
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
  only — no task-queue polling, no real escalation *detection* logic
  (`86bax0wkj`, still not built). Pause-at-will and the escalation-hook
  interface itself now exist, see `nova_escalation.py` below. Never
  merges/deletes its own worktrees, matching `nova_orchestrator.py`'s
  safety model exactly. **Dual-fuel credential switch (2026-07-16,
  `86bawpvzz` groundwork):** `dispatch_headless_task()` now picks which
  credential `claude -p` uses per run via `choose_fuel_source()` — defaults
  to the Omen's own Claude Code subscription login (confirmed live via
  `claude auth status`: Pro plan, no `ANTHROPIC_API_KEY` in the shell env)
  for hours confirmed idle against the real Claude Code activity profile
  (`/activity-profile`), falls back to the Omen's existing funded metered
  `ANTHROPIC_API_KEY` otherwise. Three decisions confirmed directly with
  Marvin rather than assumed: hardcoded `America/Chicago` timezone via
  stdlib `zoneinfo` (a real bug surfaced live here — Windows has no system
  IANA tz database, fixed by adding the `tzdata` pip package, confirmed
  with Marvin first since it's a new dependency), an hour only counts as
  idle at exactly zero messages across the profile's 60-day window (the
  strictest option offered), and any missing/ambiguous signal fails toward
  the metered key, never toward assumed-idle. Only `"zeed"`'s (the Aero's)
  activity profile counts as the human-activity signal — the Omen's own
  dispatched-task sessions would land under a different source-machine key
  if ever logged, and must never be mistaken for Marvin's own usage.
  Credential handling itself: `env -u ANTHROPIC_API_KEY -u
  ANTHROPIC_AUTH_TOKEN` immediately before the `claude` invocation for the
  subscription path (strips at exec time regardless of shell startup
  state); the metered path extracts only `ANTHROPIC_API_KEY` from `.env`
  via the Omen's own venv + `python-dotenv` rather than sourcing the whole
  file — confirmed live that headless `claude -p` uses Claude Code's native
  Bash tool (no `.mcp.json` registers `nova_tools.py`'s restricted-env
  wrapper for this path), so a blanket `source .env` would leak
  `CLICKUP_API_KEY`/`RUNPOD_API_KEY` into every tool call for no reason.
  **Verified live, both paths for real:** one real dispatch each with
  `--fuel-source subscription` and `--fuel-source api_key`, both
  `stop_reason: end_turn`. Found along the way that `claude -p`'s own
  `cost_usd` field is an estimate independent of which credential actually
  authenticated the call, not a reliable way to tell the paths apart after
  the fact — verified the real mechanism instead by directly observing
  `ANTHROPIC_API_KEY`'s presence/absence over SSH for each constructed
  shell prefix (subscription: confirmed empty; metered: confirmed present,
  without ever printing the real key)
- `nova_escalation.py` — escalation-hook stub + pause-at-will switch for
  headless dispatch, step 5 of `86bax0exx`'s checklist. Its own module
  (not folded into `nova_omen_dispatch.py`), mirroring why
  `nova_token_budget.py` isn't folded into `nova_orchestrator.py` — this
  is a plausible second caller for `nova_orchestrator.py`'s own worktree
  loop later, not just the Omen dispatch path. `check_escalation()` is a
  stub only — always `{"escalation_needed": False}` — taking the generic
  result dict `dispatch_headless_task()` already returns rather than raw
  Claude Code CLI session internals, per `86bax0exx`'s requirement that
  the interface stay backend-agnostic (Claude Code CLI today,
  OpenHands+local-model later). `is_dispatch_paused()`/
  `set_dispatch_pause()` are real, not stubbed — built 2026-07-14 after
  Marvin explicitly asked for the ability to pause the headless runner at
  will ("no simultaneous building between me and the headless session").
  State persists to `nova_state.db` (`system/dispatch_pause`, a
  deliberate extension beyond Principle 6's original list, same
  precedent as `claude_usage_history`) rather than a local JSON file —
  `nova_state.db` is the "current reality" layer a future Controller UI
  (`86bax0wkj`) would read/write anyway. `nova_omen_dispatch.py`'s
  `--pause "<reason>"`/`--resume` CLI flags are the only lever today,
  since no Controller UI exists yet. Verified live 2026-07-14: a real
  dispatch was blocked cleanly while paused (no SSH call fired), then
  fired normally end-to-end once resumed, returning a real
  `"escalation": {"escalation_needed": false}` key. **Cross-machine fix
  (2026-07-16):** `is_dispatch_paused()`/`set_dispatch_pause()` used to
  import `nova_state.py` directly — broke silently the moment anything
  checked pause state natively on the Omen (`nova_scheduled_dispatch.py`
  below), since `nova_state.py`'s `DB_PATH` is a hardcoded Windows path
  that resolves to a disconnected file on Linux (confirmed live: found
  the Omen's own accidental copy at
  `/home/marvinroyal5/nova/C:/Nova/nova_state.db`, invisible to a pause
  set from the Aero). Fixed by routing both functions through the Omen's
  own `nova_api.py` (new `POST`/`GET /dispatch-pause`) instead — same
  canonical-FastAPI-layer pattern the activity profile already uses.
  `is_dispatch_paused()` fails toward `paused=True` on any network error;
  `set_dispatch_pause()` does not fail silently, since a pause Marvin
  explicitly requests needs to be visibly confirmed, not swallowed.
  **`check_escalation()` shipped for real (2026-07-18, `86bax0wkj`)** —
  no longer a stub. Real regex parsing of a `NOVA_ESCALATION_START/END`
  block (see CLAUDE.md's Escalation Protocol subsection above for the
  exact format) out of a dispatch/resume result's own summary text, pure
  parsing/no I/O so it's reusable from both `dispatch_headless_task()`
  and the new `resume_headless_task()` in `nova_omen_dispatch.py`.
  Alongside it: `dispatch_headless_task()` now always creates an
  explicitly-named worktree and captures its real path via a before/after
  `git worktree list --porcelain` diff (needed so a resume can `cd` back
  into the exact same worktree); `resume_headless_task()` runs
  `claude -p --resume <session_id>` with no `--worktree` flag, and
  deliberately does not call `is_dispatch_paused()` — answering a direct
  question was confirmed with Marvin as a different act than a new
  autonomous run starting mid-build; and agent-log ingestion is now
  idempotent via a per-session turn cursor
  (`logs/agent_log_ingest_cursor.json` on the Omen), so a resumed
  session's earlier turns don't duplicate into the training corpus. The
  pause/package/notify/wait/resume flow this hook feeds lives in
  `nova_scheduled_dispatch.py`'s `_handle_escalation()` and `nova_api.py`'s
  new `/escalations` routes + `nova_escalations.html` UI — see the
  Escalation Protocol subsection and Section 7's route table
- `nova_omen_sync.py` — one-command sync for the Omen's MAIN checkout
  (distinct from `nova_omen_dispatch.py`'s worktree path above, which
  already self-syncs by fetching fresh from origin every run). Collapses
  the sequence that caused the earlier 15-commit stale-clone incident
  (Section 2, "HP Omen Headless Server") into one call: `git pull` →
  restart `nova-api`/`nova-chroma` (skipped if nothing new pulled) →
  confirm both are listening again via TCP probe. Deliberately
  manual-trigger only, not a git post-push hook — Marvin's explicit call,
  keeping a human decision point before new code goes live on the Omen,
  matching the review-before-merge posture `nova_orchestrator.py`'s
  worktrees already use. Requires a one-time scoped sudoers grant on the
  Omen (`NOPASSWD` for exactly `systemctl restart nova-api` and
  `systemctl restart nova-chroma`, nothing broader) — added and verified
  live 2026-07-14. Proven end-to-end the same day: real `git pull` over
  SSH, real service restart via the new sudoers grant, real TCP
  reachability confirmation on both `8001` and `8000` after restart
- `nova_task_queue.py` — readiness detection + task resolution, steps 1
  and 2 of `86bax0exx`'s checklist. `get_ready_tasks()` reuses
  `nova_clickup_client.get_unresolved_blockers()` (the same dependency-
  chain check `nova_board.py`'s `ready` command already applies) plus a
  status/description-length filter. `resolve_task_description(task_id)`
  builds the exact prompt `nova_omen_dispatch.dispatch_headless_task()`
  expects. **Two scope decisions, confirmed with Marvin 2026-07-14:**
  scope text comes from ClickUp's own `description` field, not the
  linked Google Drive doc the original spec named — Nova's runtime has
  zero Drive credentials anywhere (`.env` has only three unrelated API
  keys, no code calls the Drive/Docs API); and this stays "functions
  Marvin calls by hand" (`--list-ready`/`--resolve`/`--dispatch <task_id>`
  CLI) rather than an auto-picking loop, since `86bawpvzz` already
  flagged autonomous task selection as its own unresolved trust-boundary
  question. **Real finding from the first live `--list-ready` run:** it
  returned ~100 of the board's ~110 backlog tasks as "ready" — technically
  correct (zero ClickUp-native dependency links + a real description),
  but confirms `86bawpvzz`'s implication #3 concretely: most real-world
  blockers (financial-decision tasks, research-only tasks, a task
  literally named "gate — do before further self-hosting work" with no
  enforced ClickUp dependency) aren't encoded as dependencies at all, so
  "ready" here means "not explicitly blocked," not "safe to dispatch
  unattended." **Scheduler wired in (2026-07-16):** `get_ready_tasks()`
  now also returns each task's `tags`, and new `get_practice_queue_tasks()`
  filters further to tasks tagged `"autonomy-safe"` on the board — a
  deliberate, narrow carve-out of the "no auto-picking" rule above, not a
  reversal: auto-selection only applies within this small, hand-curated
  subset. Full-backlog auto-selection is still out of scope, still
  blocked on `86bawpvzz`'s same unresolved trust-boundary question. See
  `nova_scheduled_dispatch.py` below for the actual cron-fired consumer.
- `nova_scheduled_dispatch.py` — the real cron entry point for
  `86bax0exx`'s invocation/monitoring steps, confirmed with Marvin to fire
  every 2 hours via a **user crontab entry on the Omen** (`crontab -e` as
  `marvinroyal5`, no `sudo`) — the only viable trigger of three real
  options considered. Ruled out both of Claude Code's own scheduling
  tools first, not assumed: `CronCreate` is session-only (dies when the
  conversation that created it ends) and `RemoteTrigger`/the `schedule`
  skill bills through the metered Messages API, never touching the
  Omen's own `claude -p` subscription login — either would have silently
  defeated the whole dual-fuel design. Picks one task per firing from
  `nova_task_queue.get_practice_queue_tasks()`, dispatches via the
  existing, unmodified `nova_omen_dispatch.dispatch_headless_task()` (the
  Omen SSHes to itself over a new, dedicated no-passphrase `ed25519`
  keypair, scoped only to this loop — not reusing `id_ed25519_github`,
  same narrow-scoping discipline). Deliberately does **not** add a
  "skip SSH when local" branch to `dispatch_headless_task()` — that would
  be a second, untested code path for exactly the unattended case where
  proven behavior matters most. Transitions the ClickUp task to
  `"in progress"` only when the dispatch result carries a real
  `session_id` (a genuine round-trip happened, whether the task itself
  succeeded or reported a real blocker) — not keyed off `success`, so a
  transient SSH/timeout failure naturally retries next cycle instead of
  getting stuck, and a completed-but-blocked run still leaves `"to do"`
  for Marvin to review rather than being silently re-picked forever. A
  simple atomic-lock file (`O_EXCL`, not check-then-write) prevents
  overlap with a still-running previous firing. Logs every attempt to
  `logs/scheduled_dispatch_log.jsonl` (no rotation — same accepted,
  deferred scope as `86barby7t` at this log's much lower volume).
  **Known, accepted gap:** one task per 2-hour firing bounds the *rate*
  of new dispatches (max 12/day) but isn't the deferred review-bandwidth
  backpressure feature (`86bawpvzz` implication #2) — there's no
  awareness of how many past results are still unreviewed. The real
  mitigation is that the curated `autonomy-safe` queue is finite and
  depletes as tasks leave `"to do"`, capping unreviewed pileup at "queue
  size," not unbounded. **Review-bandwidth backpressure shipped 2026-07-16
  (`86baykvan`, `86bawpvzz` implication #2):** `record_dispatch_review(task_id,
  "merged"|"discarded", note)` appends to a new `logs/dispatch_review_log.jsonl`
  (a human calls this by hand after reviewing a dispatched task's actual
  outcome — mirrors `nova_orchestrator.py`'s `record_task_outcome()` discipline,
  keyed by `task_id` instead of branch since `scheduled_dispatch_log.jsonl`
  entries don't carry one). `count_unreviewed_dispatches()` diffs
  `scheduled_dispatch_log.jsonl` entries with a real `session_id` against
  reviewed `task_id`s. Gated behind `nova_config.json`'s
  `scheduled_dispatch.review_backpressure_enabled` (default off) +
  `max_unreviewed_dispatches` (default 3) — when on, `run_scheduled_dispatch()`
  checks this right after the pause check (before acquiring the lock) and
  returns `{"status": "review_backlog_full"}` without picking a task once the
  cap is hit. New `--review TASK_ID OUTCOME [--note]` / `--unreviewed-count`
  CLI flags. Verified live: pause check confirmed to still take priority
  (dispatch is currently paused, by Marvin's own explicit switch — the new
  gate correctly never got reached); `count_unreviewed_dispatches()` and
  `record_dispatch_review()` verified directly against seeded fake entries
  (3 unreviewed → 2 after recording one review). **Backfilled same day:**
  the two real pre-existing dispatches (`86bayjdrh` discarded — its own
  smoke-test deliverable was never actually merged, confirmed by reading
  the live file; `86baux7bb` merged — `docs/chonkie_evaluation.md`, commit
  `c7bf612`) — `count_unreviewed_dispatches()` now correctly reads 0.
  `86bayjdrh` also deleted from the board per its own stated cleanup step.
  **Observability Layer 1 shipped same day (`86baykvb7`, `86bawpvzz`
  implication #6):** `_is_clean_outcome(result)` (session_id present AND
  success True — everything else is "non-clean") gates a new
  `_post_non_clean_comment()`, called from both the resolve-failure branch
  and after `_log_outcome()` in the main dispatch path. Posts a ClickUp
  comment on the dispatched task (error/stop_reason/session_id/fuel_source/
  cost/summary, whichever fields are present) — the only notification
  channel that works today (Open WebUI push doesn't exist, Slack/email
  need credentials Nova lacks). Required adding `add_comment(task_id,
  comment_text)` to `nova_clickup_client.py` — no comment-posting function
  existed there before. Best-effort, non-fatal (a comment failure can't
  take down a dispatch that already happened), and deliberately does not
  invent stuck-run detection — `check_escalation()` stays a stub
  (`86bax0wkj`), this only reports what the result dict already says.
  Verified live against a real disposable ClickUp task (created, two real
  comments posted via `_post_non_clean_comment()` for both the "dispatch"
  and "resolve" phase formats, comment text confirmed via
  `get_task_comments`, task deleted after). Layer 2 (`/dispatch-log` route
  + dashboard tile, reconciled with `86bax4akx` first) and Layer 3 (real
  phone push, Langfuse) remain deferred per `86baykvb7`'s own layered
  design
- `nova_agent_log_status.py` — read-only cross-machine status check for
  Phase 3.5's Qwen3 8B swap trigger (~30-50 diverse real task transcripts
  in `agent_log.jsonl`, 20% held out). Closes a real gap found 2026-07-16:
  `agent_log.jsonl` exists as two separate files — the Aero's (interactive
  `nova_orchestrator.py` runs) and the Omen's (headless dispatch, converted
  from the Claude Code session transcript — see `nova_omen_dispatch.py`) —
  and nothing combined them, so "how much do we have toward the swap
  trigger" was un-answerable. Reads the Aero's local file directly, fetches
  the Omen's over SSH (same host/user/path `nova_omen_dispatch.py` already
  uses), groups both by `task_slug` (not `branch` — the Omen's converted
  entries report `"master"` for `gitBranch` on `--worktree` sessions, a
  known caveat already documented there). **Deliberately not a
  `nova_state.db` entity** — confirmed with Marvin before building, chosen
  over mirroring the `usage_history`/`activity_profile` push-and-merge
  pattern (`POST`/`GET` routes, `SessionEnd`-hook auto-push): those serve a
  live dashboard need that's real today; this only answers an occasional
  manual question, so a fetch-fresh-every-run CLI avoids adding persisted
  state + auto-push wiring ahead of a real second consumer — same
  discipline as the GPU-inference-seam and MCP-tool-calling scoping calls
  earlier in this log. **Real finding from the first live run:** raw
  task_slug count (20 combined: 19 Aero + 1 Omen) overstates true task
  *diversity* — 3 of the 19 Aero slugs are retries of the same two
  underlying tasks (the resource headroom calculator alone was attempted 3
  times before it landed), each retry getting its own worktree and its own
  task_slug. Not deduped programmatically (would need correlating task
  text/outcome across retries, more machinery than a status check
  warrants) — flagged plainly in the CLI's own printed report instead of
  presented as a clean number.

### Phase Roadmap
- Phase 0    | Foundation             | ✓ Complete
- Phase 1    | Memory Core            | ✓ Operational — Nova Log: Health, Query, and Benchmark views all live (see Section 7); only Pipeline view remains, genuinely blocked on the content pipeline (Phase 6, `86bage4ff`); log rotation still deferred
- Phase 1.5  | Self-Monitoring        | Resource headroom calculator ✓ v1 live (2026-07-05 — `nova_headroom.py`, `GET /headroom`); Task Scheduler auto-start for nova_api.py/Open WebUI ✓ shipped (`nova_watcher.py` itself still not auto-started); periodic benchmarking suite ✓ v1 live (`nova_benchmark.py --golden`) — real Llama 3.2 3B baseline established in `logs/benchmark_log.jsonl`, doubling as the eval harness for Phase 3/3.5's model-swap criteria below
- Phase 1.75 | Retrieval Intelligence | Backlog, build-first foundation laid — feature-flag system ✓ v1 live (2026-07-06, `nova_config.py`/`nova_config.json`, all flags off; `config_snapshot()` wired into every query's `query_log.jsonl` entry so flag state is tied to results before any augment exists). Remaining: A* graph traversal + document-level embeddings for the heuristic, DP context-window packing, priority-queue routing, two-tier memory decay, weighted wikilinks, link-aware ingestion upgrade — each will gate on a flag already defined above
- Phase 2    | Voice & Capture        | **Explicitly greenlit early, in progress (`86baeyg3q`, confirmed with Marvin 2026-07-12)** — Whisper Distil (STT) + Piper (TTS) + "Hey Nova" wake word + iPhone/Apple Watch quick-capture, target 2-4s full round-trip, explicit-consent-only (no passive public capture). This is a deliberate exception to the "don't build Phase 2+ without explicit instruction" rule below, not a stale status — confirm with Marvin if picking this back up after a gap to make sure the exception still stands
- Phase 2.5  | Agent Layer            | Backlog — file CRUD ✓ v1 live (`nova_tools.py`); Nova MCP Server ✓ v1 live (2026-07-06, `nova_mcp_server.py`, wraps `/ask` `/graph` `/neighbors` `/context-budget` `/ingest` as MCP tools over streamable-http:8100) but not yet wired to any MCP client or into `nova_orchestrator.py` itself — `mcp`/`httpx` are installed in `nova-env` but this is an unused standalone server until something connects to it; Docker sub-agent orchestration deferred, see Phase 3.5. **Browser Hands harness (M1) ✓ v1 live (2026-07-10, ClickUp `86barqzmv`):** `browser_hands/` package (`harness/cdp_connect.py`, `retry.py`, `selector_discovery.py`, `tree_walk.py`, `state_writer.py`, `config/sites.yaml`) — CDP-attach-only browser automation foundation, generalized from the proven `C:\Projects\developer_tools\base44_export.py` reference script. No automated login, ever (hard rule). Adapters (M2 PiSignage health-check `86barqztk`, M3 website audit `86barqzy8`, M4 subscription audit `86barr02x`, M5 Base44 conform `86barr06e`) are separate, still-backlog tasks — `browser_hands/adapters/` stays empty until then. New `playwright` dependency (pip package only, no browser-binary download needed since it only ever attaches to Marvin's own already-running Chrome). Writes to a new `browser_tasks` table in `nova_state.db`, separate from the generic `domain_state` table (an event log, not a state snapshot)
- Phase 3    | First Fine-Tune        | Backlog — Unsloth + DPO → GGUF → Ollama (conversational/lore lane). **Base-model re-eval: DONE, verdict is stay on Llama 3.2 3B (2026-07-21) — see the full evaluation-protocol paragraph at the end of this line.** **Swap trigger, now measurable:** `nova_benchmark.py --golden` (Phase 1.5) established a real Llama 3.2 3B baseline in `logs/benchmark_log.jsonl` (8 golden queries across every router category — latency, routing accuracy, fiction blend rate); a candidate model must clearly beat that logged baseline, re-running the same script, before a swap — not a fixed timeline. **Model-swap eval wrapper ✓ v1 live (2026-07-10, `nova_benchmark.py --evaluate <model>`):** one command pulls a candidate, runs the golden benchmark on it for real (fixed a real bug first — `run_golden_benchmark`'s `model_label` used to only change the log tag, never the actual model used for generation), and prints a pass/fail verdict against the logged baseline with per-metric deltas. **Dynamic model routing ✓ mechanism live (2026-07-10, `nova_config.json`'s `model_routing`, default off):** `nova_query.ask()` resolves a per-category model via `get_routed_model()` instead of a single hardcoded constant — every category maps to `llama3.2` today (the only model actually adopted), so behavior is unchanged by default; swapping in Phi-4 Mini/Qwen3 8B once either is actually adopted (still blocked on `86bagf51n`/`86bagek35`/`86bara7zk`, all backlog) is a config edit, not a code change. Verified live by temporarily routing one category to `llama3.1:8b` (the other model already pulled locally) and confirming the logged model field showed the swap really happened. **Dual-model VRAM fit — NO-GO, confirmed empirically 2026-07-11 (`86bagek35`):** `qwen3:8b` alone uses 86% of the Aero's 8GB card (real `nvidia-smi` measurement — Ollama's own `ollama ps` "SIZE" column undercounts by ~1.5GB, CUDA context/KV cache overhead isn't included there); loading `phi4-mini` alongside it evicts it every time, confirmed both directions, regardless of `OLLAMA_MAX_LOADED_MODELS`. The routing *mechanism* above is unaffected (pure config lookup, no model management), but in practice a category switch that lands on a cold model means a real load/unload latency hit, not instant switching — factor this into how aggressively categories get split across models vs. kept on one default once dual-model routing is revisited. Also surfaced: Qwen3 ships with "thinking mode" on by default, likely inflating the original `86bauwqqd` benchmark's 15728ms average — re-benchmark with `think=False` before drawing conclusions on Qwen3 8B's real fitness. Still open on `86bagek35`: context-fill latency at 8K/32K/64K/128K, quality comparison, cold-start latency, VRAM headroom with Chroma actually running (all prior tests had Chroma off — real headroom is lower now that it's live on the Omen). **Fine-tune pipeline re-scoped for Phi-4 Mini, `86bagf51n` (2026-07-21):** built `nova_finetune_phi4.py` — Unsloth QLoRA DPO training script (`unsloth/Phi-4-mini-instruct-bnb-4bit`, LoRA rank 32, LR 2e-4, batch 2 × grad-accum 4, seq len 8192, gradient checkpointing on) that reads corrected pairs directly out of `logs/training_flags.jsonl` (same file `nova_corrector.py` writes to), applies Phi-4 Mini's real chat template (`<|user|>...<|end|><|assistant|>`, confirmed live from the tokenizer, not assumed), and exports GGUF at Q4_K_M for a straight Ollama pull-back. **Confirmed with Marvin: trains fully on the Aero's own RTX 5070, not a RunPod/Vast rental** — the re-scope doc's own VRAM math (4-6GB at this config) fits the 8GB card, superseding `86baeyg1h`'s older RunPod-rental task text (written before Phi-4 Mini was locked in as the target). **Real blocker found and fixed to make that possible:** `nova-env`'s `torch` was CPU-only (`2.12.0+cpu`, `cuda available: False`) despite the RTX 5070 sitting right there unused — the same gap flagged during the voice-pipeline work, but a hard blocker here since Unsloth's QLoRA path needs CUDA (`bitsandbytes` has no CPU path). Fixed by reinstalling `torch`/`torchaudio`/`torchvision` as CUDA builds — real trial-and-error on the exact CUDA index: `cu130` installed but silently reported `cuda available: False` (driver only supports up to CUDA 12.9, cu130 needs a newer one); `cu128` matched the driver and confirmed real CUDA ops on the RTX 5070 (Blackwell, compute capability 12.0); then Unsloth's own install downgraded `torch` again to a CPU build via its transitive resolver, requiring a second reinstall pinned to `torch<2.11.0` (Unsloth's real, undeclared-in-`pip show` ceiling, only surfaced via pip's own dependency-conflict warning) — settled on `torch==2.10.0+cu128` / `torchvision==0.25.0+cu128` / `torchaudio==2.10.0+cu128`. `transformers` also moved `5.10.2` → `5.5.0` as part of Unsloth's resolver — re-verified live that `nova_voice.py`'s `distil-whisper` STT pipeline still loads and now runs on the GPU instead of CPU, a real bonus fixing the voice pipeline's previously-flagged CPU-only slowness as a side effect. `requirements.txt` regenerated via a full `pip freeze` (this repo's established convention — every transitive dependency pinned, not a curated top-level list). **Verified live, not just reviewed:** a real `--dry-run` run against the 11 existing corrected pairs loaded the actual quantized model, attached a real LoRA adapter (17.8M trainable params, 0.46% of 3.85B), and ran 3 real DPO training steps on this hardware — loss dropped 0.693 → 0.414 and `rewards/accuracies` hit 1.0 by the third step, confirmed mechanically working end-to-end, entirely within the 8GB card. **Explicitly not done, per the doc's own floor:** GGUF export wasn't exercised live (no real trained adapter exists yet to export), and `run()` hard-refuses a non-dry-run invocation below `MIN_REAL_PAIRS = 100` — real corrected pairs are still only 11 (unchanged since 2026-07-05), so `86baeyg1h` (the actual production run) stays blocked on accumulation, not on anything this task was scoped to fix. `nova_config.json`'s `model_routing.default_model` was deliberately **not** flipped to `phi4-mini` — that's `86bagek35`'s call once its own routing validation (still in progress) lands, not this task's. **`86bagek35` closed out same day (2026-07-21):** its own remaining checklist — context-fill latency at 8K/32K/64K/128K, cold-start latency, VRAM headroom with Chroma running — is now fully addressed. Added `test_context_fill()`/`run_context_fill_benchmark()` and `test_cold_start()` to `nova_benchmark.py` (new `--context-fill MODEL`/`--cold-start MODEL` CLI flags) — distinct from the pre-existing `test_context_size()`, which only allocates an empty `num_ctx` buffer and times a short prompt; the new function fills the context with real content up to each target size (measured with Phi-4 Mini's actual Hugging Face tokenizer, not a char-count guess) before timing generation, which is what "context fill latency" actually asks for. **Real numbers, run live against `phi4-mini` on the Aero:** 8K → 7.4s, 32K → 15.8s, 65K → 58.2s, full 128K → 171.2s (~2m51s) — latency scales with context size as expected, no failures at any size. Cold start (unload via `ollama stop`, then time a fresh request): 4.3s. **"VRAM headroom with Chroma running" is now moot, not measured** — Chroma moved off the Aero entirely to the Omen (`HttpClient` over the network) after this task was originally scoped, so it no longer competes for local VRAM regardless of what's loaded in Ollama. Found and fixed a real latent bug while building these: both new functions originally called bare `ollama.chat()` (matching `test_context_size()`'s own existing pattern) and failed outright with `OLLAMA_HOST=0.0.0.0` set in the shell — the exact bind-all-address gotcha `nova_query.py` already guards against via its own `ollama_client`; fixed by reusing `nova_query.ollama_client` instead (the pre-existing `test_context_size()` still has this same latent bug, left untouched since fixing it wasn't part of this task). One minor, harmless finding: the context-fill prompt builder overshot the 128K target by 8 tokens (131,080 vs. 131,072), tripping a benign tokenizer length warning — Ollama processed it successfully anyway, noted rather than hidden. **Full base-model evaluation protocol run for real, same day (2026-07-21) — verdict: stay on Llama 3.2 3B.** Prompted by the Phi-4 Mini speed finding above contradicting the hardware doc's "2-3x faster" claim: rather than trust any doc claim again, ran every real candidate through Nova's own golden-query suite (the actual metric the Phase 3 swap trigger gates on) fresh, in one session, to eliminate cross-day noise. **Caught a second real doc error first:** "Llama 3.3 8B" in the Hardware Profile & Model Comparison doc does not exist — Meta only ever released Llama 3.3 as 70B (43GB, confirmed via Ollama's own library listing) — dropped from the evaluation entirely, nothing to test. Added `gemma3:4b` (confirmed real, 3.3GB) as the replacement fifth candidate. **Fixed Qwen3's thinking-mode measurement bug for real this time:** added a `think` parameter threaded through `nova_query.ask()` → `nova_benchmark.py`'s `_run_single_golden_query()`/`run_golden_benchmark()`/`evaluate_candidate()` (new `--no-think` CLI flag; `None` by default, so every existing call site is behaviorally unchanged), forwarding to `ollama.Client.chat()`'s own `think` kwarg only when explicitly set. Also added a `think` field to logged `benchmark_log.jsonl` entries so a `think=False` run is distinguishable from an old default-thinking-mode one without re-deriving it from latency alone. **Real results, all fresh same-session runs:** llama3.2 (baseline) 3135ms; llama3.1:8b 4251ms (FAIL); phi4-mini 4247ms (FAIL); qwen3:8b with `think=False` 7133ms — down from the old default-thinking 15728ms, confirming that bug was real, but still FAIL; gemma3:4b 4481ms (FAIL). Blend rate tied at 0% across every model this round (including phi4-mini, which showed 33.3% on the earlier single-run test — a reminder that blend detection is noisy run-to-run and single-sample comparisons across different days aren't reliable). **Every real candidate fails Nova's own swap criteria, unambiguously** — Llama 3.2 3B is the fastest model on this actual RAG workload on this hardware, not close, despite having the weakest general benchmarks (MMLU/HumanEval) of the five per the hardware doc. Not treated as a permanent verdict — these are 8 simple sanity queries, not a stress test of reasoning depth on hard queries, so this specifically answers "is a swap justified today," not "will a swap ever be justified"
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
├── nova_log_rotation.py    # Weekly rotation for the Nova Log telemetry files — archive >90d + keep last 1000, non-destructive (86barby7t)
├── nova_sources.py         # Source paths config — Second Brain location
├── nova_tools.py           # Path-scoped file/exec primitives for the coding sub-agent
├── nova_orchestrator.py    # Coding sub-agent loop (Claude-backed v1, git-worktree isolated)
├── nova_orchestrator_graph.py # LangGraph port of the turn loop (langgraph_orchestration flag, default off)
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
├── nova_omen_sync.py       # One-command sync for the Omen's main checkout — git pull, restart nova-api/nova-chroma, verify listening
├── nova_task_queue.py      # Readiness detection + task resolution for headless dispatch (86bax0exx steps 1-2)
├── nova_scheduled_dispatch.py # Cron-fired entry point on the Omen — picks + dispatches one autonomy-safe-tagged task every 2 hours
├── nova_agent_log_status.py # Read-only Aero+Omen agent_log.jsonl merge — Qwen3 8B swap-trigger progress check
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

### Working Directly on the Omen via SSH (away from the Aero)
When SSHed into the Omen to do real work away from the Aero — a real interactive/manual
session, not the automated `nova_omen_dispatch.py` headless path — **never edit the main
checkout (`~/nova`) directly.** That directory is a one-directional deployment target:
`nova_omen_sync.py` pulls into it and restarts `nova-api`/`nova-chroma`. Hand-editing and
pushing from there recreates the exact two-way drift that caused the 15-commit stale-clone
incident documented above ("Important gap in the 'COMPLETE' verification above").

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

**Two more real gotchas hit doing this live, 2026-07-25, worth knowing before it looks like a
mystery failure:**
- **No git identity configured on the Omen's main repo** — every commit that's ever landed
  there came from a push made elsewhere, then pulled via `nova_omen_sync.py`, so `user.name`/
  `user.email` were never set locally. A commit made directly on the Omen needs `git -c
  user.name='...' -c user.email='...' commit ...` (scoped to that one command, not a global
  config change) until/unless this gets set up permanently.
- **A plain `ssh host "command"` is non-interactive and doesn't source `.bashrc`**, so PATH is
  missing `~/.local/bin` — this silently breaks the `gitleaks` pre-commit hook (the binary is
  genuinely installed there per `86bawk37h`, just invisible to this invocation style). Fix:
  prepend `PATH=$HOME/.local/bin:$PATH` explicitly in the SSH command.

No new tooling required for the worktree/push mechanics — this is exactly the `git worktree
add` + fetch-fresh-from-`origin` pattern `nova_orchestrator.py`'s `_create_worktree()` already
uses, just applied by hand instead of by the dispatcher, with the push step now correctly
routed through the Aero. Once the Nova Controller (`86bax0wkj`/`86baxahn7`) exists, this manual
SSH workflow is expected to mostly be replaced by triggering `nova_omen_dispatch.py` from the
Controller UI instead, which already self-syncs from `origin` on every run and never needed the
Omen to push in the first place (it dispatches over its own separate SSH-to-self keypair, and
`nova_omen_sync.py`/`nova_omen_dispatch.py` both only ever pull).

### Omen Capacity Audit (86baxty6d, self-hosting gate) — 2026-07-21
This task exists because every self-hosting decision so far (Chroma, Ollama callback,
Dockerized services, headless dispatch, the still-unscoped Langfuse/Vaultwarden/self-hosted-git/
Obsidian-CouchDB-sync ideas) was scoped individually, assuming "the Omen can host this," with
nobody ever checking the sum — flagged 2026-07-13 as a gate: no further self-hosting tasks
(`86baxtb4m` Obsidian migration, `86bau47mb`, `86baf4e29`, `86bax697m`) proceed until this audit
happens, revisited periodically as new services get proposed, not just once.

Built `nova_omen_capacity.py` — SSHes from the Aero (same Tailscale-IP connection details as
`nova_agent_log_status.py`) and pulls a real CPU/RAM/disk/GPU snapshot plus what's actually
running, rather than trusting an assumption. Appends one line per run to
`logs/omen_capacity_log.jsonl` — this is the task's own "own growth-rate tracking, not just a
one-time snapshot" requirement, satisfied by making the check cheap enough to re-run instead of
building a live monitoring stack for services that don't exist yet.

**Real findings, run live 2026-07-21:**
- **Compute headroom is large:** 8 CPU cores at near-zero load (0.04 avg), 6.42GB of 7.64GB RAM
  available (84% free), 75.4GB of 97.9GB disk available (77% free), swap barely touched.
- **Because almost nothing is actually deployed yet** — the only two persistent (always-on)
  services are `nova-api.service` and `nova-chroma.service`, both lightweight. Every other
  self-hosting idea (Langfuse's ClickHouse+Postgres+Redis stack, Vaultwarden, self-hosted git,
  Obsidian CouchDB sync) is still `[Initiative — not scoped]` on the board — there is currently
  nothing to model resource competition against for the task's "split always-on vs. bursty
  services" scope item beyond these two. Docker itself is installed and running
  (`docker.service` active) but completely empty — 0 containers, 0 images — a clean, ready
  starting point for whenever `86baf4e29` (Dockerize) actually happens, but nothing to
  "consolidate to one source of truth" (the task's 4th scope item) yet since there's nothing
  ad-hoc to consolidate.
- **Disk breakdown:** Chroma's real data directory is only 0.02GB (mirrors the Second Brain's
  actual size, not explosive), the git repo is negligible, and the one real disk consumer is the
  Python venv itself (5.53GB, fixed-size — doesn't grow the way a database's trace/vector store
  would). No component with a genuine unbounded growth trajectory exists on the Omen today.
- **GPU confirmed present but unusable, not just underpowered:** `lspci` confirms a real GTX 1050
  Ti Mobile (GP107M) — but zero NVIDIA driver is installed (`dpkg -l | grep nvidia` returns
  nothing), so it's not a factor in capacity planning at all right now, consistent with (and a
  stronger version of) the existing "service-host-only, not inference-capable" framing.

**Verdict: gate open for today's actual headroom, not a blanket clearance for everything
queued.** Real capacity is not remotely a constraint right now, precisely because most of the
self-hosting backlog hasn't been built yet — this audit answers "is there room for the next
reasonable increment," not "will Langfuse's full stack fit," since Langfuse has no real resource
spec to check against until it's actually scoped. **Recommendation, not yet acted on:** re-run
`nova_omen_capacity.py` before and after each individual self-hosting task actually gets
deployed (starting with whichever of `86baxtb4m`/`86bau47mb`/`86baf4e29`/`86bax697m` gets picked
up next), watching RAM specifically — it's the smallest absolute pool of the three (7.64GB
total) and the one a multi-service database stack (Langfuse's Postgres+ClickHouse+Redis) would
plausibly pressure first, well before CPU or disk become a concern.

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
`_handle_escalation()` registers it with `nova_api.py`'s `POST /escalations` (never a
direct `nova_state.py` import — see that file's `pending_escalations` entity note: the
same cross-machine hardcoded-`DB_PATH` bug that already broke `dispatch_pause`), tags
the ClickUp task `awaiting-answer`, and comments the question. Marvin answers via
`GET /escalations-ui` (`nova_escalations.html`); the answer is accepted immediately
(fire-and-forget `BackgroundTasks`), and `nova_omen_dispatch.resume_headless_task()`
runs `claude -p --resume <session_id>` in the background, `cd`'d into the exact
original worktree (never re-passing `--worktree`, which would create a new one).
`dispatch_headless_task()` always creates an explicitly-named worktree now
(`nova-dispatch-<uuid8>`, never the bare `--worktree` flag) and captures its real path
via a before/after `git worktree list --porcelain` diff, since `claude -p`'s own JSON
result never reports it — safe because `nova_scheduled_dispatch.py`'s atomic lock file
already serializes every cron-triggered dispatch (a manual `--dispatch` call bypassing
that lock is a documented, accepted narrow gap). The resumed result goes through the
identical `handle_dispatch_outcome()` logic shared with a fresh dispatch — can escalate
again, uncapped, or finish clean/non-clean like any other run.

**Four decisions confirmed with Marvin:** (1) resuming an escalated session is **not**
blocked by the global dispatch-pause switch — answering a direct question is a
different act than a new autonomous run starting while he's mid-build; (2) agent-log
transcript ingestion is now genuinely idempotent via a per-session turn cursor
(`logs/agent_log_ingest_cursor.json` on the Omen) — a resumed session's earlier turns
must not duplicate into the Qwen3 training corpus; (3) `POST /escalations/{id}/answer`
requires header `X-Nova-Escalation-Token` matching env var `NOVA_ESCALATION_TOKEN` —
the first cost-incurring write route on `nova_api.py`'s otherwise-unauthenticated
Tailscale-only surface, ahead of the general token-auth ticket (`86bawf2z2`); (4) a
task awaiting an answer gets ClickUp tag `awaiting-answer`, not just a comment.

**Review-backpressure interaction (found and fixed same day):** a paused-for-escalation
dispatch has a real `session_id` but `success` is not `True` — without a fix, it would
count against both the review-backpressure cap (`86baykvan`, a "not done yet" task
counted as "done and unreviewed") and fire a non-clean-outcome ClickUp comment
(`86baykvb7`) alongside the escalation comment. Fixed via `handle_dispatch_outcome()`
checking escalation first (mutually exclusive `if`/`elif` with the clean/non-clean
branch) and `count_unreviewed_dispatches()` excluding `pending`/`resuming` task_ids via
new `_pending_escalation_task_ids()` (fails toward an empty/non-excluding set on error,
keeping the cap conservative).

**Manual step required:** Marvin must set `NOVA_ESCALATION_TOKEN` in the Omen's `.env`
and restart `nova-api` (or run `nova_omen_sync.py`) before the answer route will accept
anything — it 401s otherwise, by design (fail-closed, not a soft pass).

### Task Tiering (86bb01wur, 2026-07-19)
Extends `nova_task_queue.py`'s existing `autonomy-safe` batch-tag gating (Phase-4-era
scheduled dispatch, see "Scheduler wired in" above) with a per-task decision made at
creation/rescope time instead of a later sweep. Nova proposes an autonomy tier
(`autonomous` / `needs review` / `manual only`) + a qualitative confidence
(`low`/`medium`/`high`) + one-sentence reasoning via `propose_tier()` — a single
non-agentic Claude completion, not the full agent loop (mirrors `nova_corrector.py`'s
`request_correction()` pattern). Detection is polling-based inside
`nova_scheduled_dispatch.py`'s existing 2-hour loop
(`nova_task_queue.detect_tier_candidates()` diffs each task's real `date_updated`
against a stored per-task watermark) — no ClickUp webhooks exist anywhere in this
codebase, confirmed by grep, so push-based detection isn't available without new infra.

Reuses `86bax0wkj`'s exact propose→register→notify→answer shape: `system/
pending_tier_proposals` + `system/task_tier_watermarks` in `nova_state.db`, new
`/tier-proposals`/`/tier-watermarks` routes, a decide route reusing the same
`X-Nova-Escalation-Token`. The `autonomous` tier maps to the exact existing
`autonomy-safe` tag string (`TIER_TAGS`) — `get_practice_queue_tasks()` needed zero
code change, and every already-hand-tagged task keeps working with no migration.
Accept is one tap, comment optional (weak positive signal — confirms the guess was
right); override requires real reasoning (strong signal) — together these are the
first real mechanism for `86bax8bb5`'s capability-understanding differential scorer,
which had sat parked with no way to generate its own comparison data.

Only plausibly-dispatchable tasks get tiered — exploratory `"Spec:"`-prefixed tasks
are skipped (`_is_tierable()`). A `--sweep-tiers [--limit N]` CLI flag on
`nova_task_queue.py` does the retroactive backlog backfill, reusing the identical
`register_tier_proposal()` pipeline the ongoing poll uses — not a separate bulk-apply
path. `--limit` deliberately skips persisting watermarks, for testing a small subset
before committing to the real full sweep.

**Two real bugs found and fixed during live verification, not code review:**
(1) `detect_tier_candidates()` originally persisted the watermark map as a side
effect of merely being called — a pure inspection call with no intent to process
anything silently marked the whole backlog "seen," which would have quietly
defeated the retroactive sweep before it ever ran. Fixed by splitting detection
(pure read/diff) from `persist_tier_watermarks()` (explicit, called only after a
caller has actually attempted every candidate). (2) `propose_tier()` didn't strip
markdown code fences from Claude's response — Claude sometimes wraps its JSON in
` ```json ... ``` ` despite being told not to, which silently tripped the
fail-toward-restrictive fallback (`manual only`/`low`) on a real proposal. Fixed to
strip a leading/trailing fence before parsing.

### Nova Controller UX (86baxahn7, 2026-07-19)
The real UX layer on top of `86bax0wkj`'s backend — one reverse-chronological Feed
(`nova_controller.html`, served at `GET /controller`) replacing separate dashboards,
per Marvin's 2026-07-13 framing (lift interaction primitives from social media,
explicitly reject engagement-optimization mechanics — no unread badges, no
streak-as-pressure UI, no engagement-ranked ordering; strictly chronological sort).
`/escalations-ui` now redirects to `/controller`; `nova_escalations.html` itself was
retired, its escalation/tier-proposal card logic ported in as-is.

**Scoped to real data only, checked before building** (this project's standing
discipline): the Feed merges escalations, tier proposals, dispatch outcomes
(new `GET /dispatch-log`, reading `scheduled_dispatch_log.jsonl` +
`agent_task_outcomes.jsonl`), and tool-call/blend-flag swipe-labeling prompts
(new `GET /label-queue`, reading `tool_call_log.jsonl` where `was_necessary is None`
and `training_flags.jsonl` where `correction == ""`). Tutor-prompt and
differential-scorer card types are **not built** — no `nova_tutor*.py` or
`nova_differential*.py` file exists anywhere (confirmed by grep), both are pure
ClickUp backlog. The Discover tab shows an honest "not built yet, depends on Nova
Tutor" line instead of a fake placeholder card.

**The swipe-labeling cards are the real UX target** the ticket names explicitly —
`was_necessary`/`was_used` and `correction` have sat `null`/`""` waiting for exactly
this kind of judge-pass since those logging modules were built. Hand-rolled
`touchstart`/`touchmove`/`touchend` gestures (`translateX` drag-follow, a
distance threshold to commit) — no gesture library, since nothing in this repo's
frontend uses a bundler/npm and a CDN script would fight the PWA's offline
app-shell caching. A tap (no meaningful drag) falls back to the same two choices as
buttons, matching the ticket's own "tap-or-expand-to-text" universal card action.
`POST /label-queue/{kind}/{id}/decide` (token-gated) patches the matching
`tool_call_log.jsonl`/`training_flags.jsonl` entry in place (read-all/rewrite-all,
same idiom as `nova_corrector.py`'s `load_entries()`/`save_entries()`) — a known,
accepted concurrency limitation is documented directly in the route's own docstring
(`tool_call_log.jsonl` is actively appended to by this project's own tool-call
logging hook during any live session; real file-locking isn't justified for a
personal, single-user, human-triggered write against this risk profile).

**PWA**: `manifest.json` + two hand-written flat PNG icons (`icon-192.png`/
`icon-512.png`, generated via raw `zlib`/`struct` — no Pillow, confirmed not
installed, and a flat two-color placeholder mark doesn't need an imaging library;
swappable for real branding later with zero code change) + `sw.js`, a service
worker caching **only the app shell** (this page's own HTML/CSS/JS/icons), never
live data — stated explicitly so "offline" never means "stale escalation data."
Verified live: real browser load at phone width (390×844), zero console errors,
a real label decision confirmed on disk over SSH after clicking the button in the
actual browser (`was_necessary`/`was_used` both flipped from `null` to `true`).

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

### Training-Data Accumulation Oversight (86bax4akx, 2026-07-21)
Closes the gap named 2026-07-13: existing tasks *build* or *consume* training sets
(`86bara7pn` curates coding data, `86baeyg1h` consumes DPO pairs) but nothing tracked
accumulation *as it happens* — `86baeyg1h`'s own task text says "currently 11 pairs, keep
accumulating" as a static line, not a live count.

**Real spec-vs-repo check before building** (this project's standing discipline): the
ticket's full scope named 5 items plus an anti-poisoning mechanism. Two of the five don't
have a real data source to build against — a "tutor-domain `blend_flag` log" (Nova Tutor is
entirely unbuilt, confirmed by grep, same finding `86baxahn7` already made) and a
"coding"-domain coverage bucket (`86bara7pn` is still blocked, zero coding DPO pairs exist).
Built what's real instead of stubbing those two.

**Built:**
- `GET /training-data-status` (`nova_api.py`) — live count from `logs/training_flags.jsonl`
  computed fresh on every call: total flagged, total corrected (real DPO pairs), coverage by
  `nova_router.py`'s real categories (not the ticket's aspirational lore/tutor/coding split),
  and threshold status against `MIN_REAL_PAIRS_FOR_FINETUNE = 100` — duplicated from, not
  imported from, `nova_finetune_phi4.MIN_REAL_PAIRS`, so `nova_api.py` (the always-running
  production server) never depends on the training stack (`datasets`/`unsloth`/`torch`) being
  installed. A comment cross-references the source of truth so the two can't silently drift
  without a visible TODO.
- New `dpo_verify` kind on the existing `/label-queue` + `/label-queue/{kind}/{id}/decide`
  mechanism (86baxahn7's swipe-card infrastructure, reused rather than a new endpoint) — the
  real three-state shape the ticket asked for (unverified → confirmed-good / needs-rework),
  distinct from the pre-existing `blend_flag` kind (which asks "does this need a correction
  written," not "is the correction that got written actually good"). Same synthetic
  `line:<index>:<timestamp>` id scheme, same token-gated decide route, same
  read-all/rewrite-all idiom as `blend_flag` and `tool_call`.
- `nova_controller.html` — a small persistent "DPO pairs toward fine-tune" status widget
  (progress bar + category/verification breakdown, refreshes every 15s) alongside the
  existing Board Watch toggle, plus a `renderLabelCard()` branch for `dpo_verify` cards and a
  help-panel entry explaining the new card type.

**Real bug found and fixed during live verification, not code review:** testing
`/label-queue` at its real default `limit=50` (not a large test limit) showed **zero**
`blend_flag`/`dpo_verify` entries in the response — `tool_call` entries are so much more
numerous (2400+) and recent that a single merge-then-truncate silently starved out every
training-data card, meaning the rarer, higher-value human judgments this whole feature exists
for were **never reaching the real Feed at all**, a pre-existing gap this work made
concretely visible rather than introduced. Fixed by capping each kind at `limit`
independently before merging, so a busy tool-call day can no longer hide every blend-flag or
DPO-verification card. Verified live: `/training-data-status` returned real numbers (57
flagged, 11 corrected, 89 remaining, 11.0%, all 11 in `fiction`, all unverified) matching a
manual count exactly; `/label-queue?limit=5000` confirmed all 11 real corrected pairs surface
as `dpo_verify` entries with real correction text; the decide route confirmed 401 without a
token (fail-closed, same as every other token-gated route).

**Explicitly deferred, with real reasons, not stubbed:** tutor-domain and coding-domain
coverage (no data source yet — see above); the anti-poisoning statistical outlier check (the
ticket's own text ties it to auto-promotion "via the Langfuse dataset-promotion pipeline,
`86bax697m`," which hasn't been adopted yet even though newly unblocked by `86baxty6d` — and
a distribution of 11 points is too thin for outlier detection to mean anything yet regardless
of Langfuse); literal reuse of the tool-call audit pattern (`86bawntpb`/`86bawntpm`) — that
async judge-pass doesn't exist either, so this followed the same *conceptual* shape via the
already-working Controller swipe-card mechanism instead of a nonexistent reference
implementation.

**Real incident, same day:** Marvin loaded `/controller` on the Omen via Tailscale and saw the
new widget show 0/100, not the real 11/100. Root cause: `nova_logger.py`'s `LOGS_DIR` and
`nova_corrector.py`'s `JSONL_PATH`/dotenv path were **both still hardcoded to
`"C:/Nova/..."`** — the same bug class as `GRAPH_PATH`/`CONFIG_PATH`/`CHROMA_HOST` above, just
never caught here before because nothing had exercised these two files' Omen-side behavior
until this feature made it visible. Confirmed via SSH: the Omen has **no `training_flags.jsonl`
file at all**, not even a misplaced one (checked for the same accidental-nested-folder pattern
`nova_state.db`'s bug left behind — found nothing, meaning `log_blend()` has likely just never
fired for real on the Omen yet, not that it's been silently corrupting data). Fixed both to
resolve relative to their own file location, verified live on the Aero (both now resolve to
the real, existing `training_flags.jsonl`). `nova_corrector.py`'s `SECOND_BRAIN` path is
deliberately left hardcoded — Marvin's OneDrive vault is genuinely Aero-only, not a portability
bug.

**Broader pattern check, same incident:** grepped the whole codebase for `C:/Nova` — found 17
more real instances beyond the ~8 already fixed across this project's history, including one
with likely real user-facing impact (`nova_log.py`'s `LOGS_DIR`, meaning the Nova Log Health
dashboard probably has this identical bug on the Omen right now). Filed as a dedicated task,
**`86bb1pkpb`**, rather than fixed ad hoc — two of the seventeen (`nova_state.py`'s `DB_PATH`,
`nova_tools.py`'s venv Scripts path) need a different fix shape than a relative-path swap
(architectural routing and OS-conditional logic respectively), so this needs real per-file
review, not a blind sweep.

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
| /escalations-ui | GET | ✓ Working | Redirects to /controller (86baxahn7) |
| /controller | GET | ✓ Working | Nova Controller Feed page (HTML, PWA-installable) — nova_controller.html |
| /dispatch-log | GET | ✓ Working | Merged dispatch/outcome history (scheduled_dispatch_log.jsonl + agent_task_outcomes.jsonl) |
| /in-flight-status | GET | ✓ Working | Is a headless dispatch running right now, and if so which task — backs the Controller's live status widget (86bb3cey0), headless-dispatch lane only |
| /flags | GET | ✓ Working | Current value of 7 important boolean flags (6 in nova_config.json + dispatch_pause) — backs the Controller's switches panel (86bb3d725) |
| /flags/{flag_key} | POST | ✓ Working | Toggle one flag — token-gated. Config-file flags commit locally on whichever machine handled the request but do NOT auto-push (the Omen's deploy key is read-only) |
| /label-queue | GET | ✓ Working | Unlabeled tool-call/blend-flag/dpo-verify entries awaiting a judge-pass — each kind capped at `limit` independently before merging (86bax4akx fix) |
| /label-queue/{kind}/{id}/decide | POST | ✓ Working | Patch a label decision — token-gated (X-Nova-Escalation-Token) |
| /training-data-status | GET | ✓ Working | Live DPO pair count, category coverage, verification status vs. the fine-tune floor (86bax4akx) |
| /tier-proposals | POST/GET | ✓ Working | Register/list pending autonomy-tier proposals (86bb01wur) |
| /tier-proposals/{id}/decide | POST | ✓ Working | Accept/override a tier proposal — token-gated |
| /tier-watermarks | GET/POST | ✓ Working | {task_id: last_seen date_updated} for tier-proposal creation/rescope detection |

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
in Sections 1 and 2's file-by-file writeups — this table is a terse date-ordered index, not the
source of truth. Search Section 1/2 by filename or ClickUp ID for the full story.

| Date       | Change                                        |
|------------|------------------------------------------------|
| 2026-06-15 | CLAUDE.md created |
| 2026-06-15 | Documented /context-budget bug (Section 5) |
| 2026-07-04 | Marked /context-budget fixed; added Open WebUI OpenAI-compat routes/launch scripts; added Nova Log v1 (query_log.jsonl + /nova-log Health dashboard) |
| 2026-07-05 | Reconciled Phase Roadmap with ~40 ClickUp tasks (added Phases 1.75/3.5/6); shipped Phase 3.5 v1 coding sub-agent (`nova_tools.py`/`nova_orchestrator.py`, worktree-isolated, Claude-backed); merged first sub-agent task (`nova_headroom.py`, commit `c516cca`); defined Phase 3/3.5 swap triggers, re-scoped Phase 4 lightweight; shipped Phase 4 v1 (Tailscale, auto-start, firewall rules); added `record_task_outcome()`, `/code` router prefix, Nova Log Query view, golden-benchmark suite (Llama 3.2 3B baseline); added metrics legend to `nova_log.html` |
| 2026-07-06 | Shipped standalone `nova_mcp_server.py` (unwired, port 8100); added prompt caching to orchestrator/corrector Claude calls; shipped feature-flag system (`nova_config.py`/`.json`, all off) |
| 2026-07-10 | Documented LangGraph decision (`86bat0u81`); shipped LangGraph orchestration v1 (`nova_orchestrator_graph.py`, gated off); shipped dynamic model-routing mechanism + model-swap eval wrapper (`--evaluate`); shipped Browser Hands harness M1 (`browser_hands/`, CDP-attach only, no login) |
| 2026-07-11 | Migrated Chroma `PersistentClient`→`HttpClient` (Omen-hosted) across `ingest.py`/`graph_builder.py`/`nova_query.py`; added `nova_board.py`/`nova_clickup_client.py`/`nova_chroma_omen_check.py`; fixed hardcoded-Windows-path bug in `nova_orchestrator.py`'s dotenv load |
| 2026-07-12 | Added `nova_status_digest.py`/`NOVA_STATUS.md`; Omen Ubuntu setup verified through step 9/11; added uncommitted-changes check to Sections 8/11; **`86baeyfm1` (Omen headless server) marked complete** — Tailscale live, full validation passed; documented Phi-4/Qwen3 VRAM NO-GO and Phase 2 early greenlight; added end-of-turn CTA requirement (Section 8); shipped `nova_chunk_viz.py` (CLI stage) and `nova_embedding_viz.py`/`.html`; found+fixed Omen's 15-commit-stale git clone (passphrase-protected deploy key) and `GRAPH_PATH` hardcoded-path bug; added "Known At-Risk Character Pairs" to Section 6; **`86bawfn19` marked complete** — `nova_api.py` live on Omen independent of Aero |
| 2026-07-14 | Shipped `nova_omen_sync.py` (pull→restart→verify); shipped `nova_escalation.py` pause-at-will switch (`check_escalation()` still a stub); documented standard git strategy (push→sync) in Section 8; shipped `nova_task_queue.py` readiness detection + task resolution |
| 2026-07-15 | Shipped security-cluster fixes: `nova_tools.py` `run_command` hardening (`cd`/PATH/env restrictions), `resolve_task_description()` prompt-injection boundary language, `.env` git-ignore audit; committed `graphify-out/` knowledge graph + documented Omen SSH worktree workflow; added Claude Code activity profile (`nova_usage_logger.py`, `/activity-profile`) |
| 2026-07-16 | Shipped dual-fuel credential switch for headless dispatch (`choose_fuel_source()`, fixed a Windows-`zoneinfo` bug via `tzdata`); shipped `nova_scheduled_dispatch.py` cron trigger (2hr, `autonomy-safe` tag) — found+fixed a cross-machine `dispatch_pause` bug (`nova_state.py`'s hardcoded `DB_PATH`) along the way; shipped `nova_agent_log_status.py` (Aero+Omen `agent_log.jsonl` merge, Qwen3 swap-trigger progress); shipped review-bandwidth backpressure (`86baykvan`, fixed `nova_config.py`'s hardcoded `CONFIG_PATH` first); backfilled review decisions, shipped observability Layer 1 (ClickUp non-clean-outcome comments) |
| 2026-07-18 | Shipped Nova Controller v1 Escalation Answer UI (`86bax0wkj`) — real `check_escalation()` regex parsing, `resume_headless_task()`, `/escalations` routes, `nova_escalations.html`; fixed a review-backpressure/escalation double-count interaction and 3 hardcoded-path 500s found during live verification |
| 2026-07-19 | Merged `86baxbt82` into `86bawf2z2` (auth-layer tickets); shipped Task Tiering (`86bb01wur`) — `propose_tier()`, `/tier-proposals`; shipped Nova Controller UX (`86baxahn7`) — `nova_controller.html` Feed/swipe-labeling, `/dispatch-log`, `/label-queue`, PWA shell; shipped `nova_remote_inference.py` RunPod adapter (gated, wired into `ask()`); shipped `nova_agentic_dataset_curator.py` (10K-row curated dataset); shipped `nova_voice.py` Minimal-tier voice (wake word + local STT/TTS), fixed `CHROMA_HOST` to Omen's Tailscale IP (was LAN IP, broke off-network) |
| 2026-07-21 | Shipped `nova_finetune_phi4.py` Phi-4 Mini QLoRA DPO script (CUDA `torch` reinstall required, verified 3 real DPO steps); closed out `86bagek35` context-fill/cold-start benchmarks; ran full base-model evaluation protocol — **verdict: stay on Llama 3.2 3B**, no candidate beats baseline; shipped `nova_omen_capacity.py` self-hosting-gate audit — verdict: headroom open today; shipped training-data accumulation oversight (`86bax4akx`) — `/training-data-status`, `dpo_verify` label kind, fixed a `/label-queue` truncation bug; fixed hardcoded `C:/Nova` paths in `nova_logger.py`/`nova_corrector.py` (filed `86bb1pkpb` for 15 more instances) and in `nova_log.py`/`nova_benchmark.py` |
| 2026-07-25 | Shipped `nova_log_rotation.py` (`86barby7t`, merged PR #9) — weekly, non-destructive rotation of the Nova Log telemetry files (`query_log.jsonl`/`benchmark_log.jsonl`): archives entries >90 days old and past the most-recent 1000 into month-stamped files under `logs/archive/`, atomic active-file rewrite. Standalone cron CLI (`--dry-run`/`--file`/`--max-age-days`/`--max-active`), not wired into the deferred `nova_watcher.py`. Deliberately scoped to the two real Nova Log files only — the other append-only JSONL logs (`training_flags.jsonl`, `tool_call_log.jsonl`, `agent_log.jsonl`, `scheduled_dispatch_log.jsonl`) feed full-history consumers and need a per-log decision before rotating. Live `--dry-run` against the real Aero logs confirmed clean before merge (158/12 entries, both unchanged, no errors) — closes the gap this task's own dispatch flagged (see next). **Merged PR #8** — closed the `run_command()` absolute-path-argument gap (`_command_references_outside_root()`), independently re-verified with real test cases before merging, not just trusted from the PR body. **Real end-to-end sandboxed-cron-dispatch test run for real** (first genuine unpaused firing through `run_scheduled_dispatch()`, not just monkeypatched isolation) — surfaced and fixed a serious bug: `--permission-mode acceptEdits` never approves the Bash tool, only file edits, confirmed via a real `permission_denials` entry on a bare `python3 -c "print(2+2)"` call outside Docker entirely. This affected **both** headless dispatch paths since 2026-07-14, unnoticed because no prior real dispatch had needed to execute code. Fixed by switching only the sandboxed path to `--permission-mode bypassPermissions` (safe there specifically because the Docker mount boundary is real, verified containment) — deliberately left the bare-SSH path alone, since bypassing permissions with no container underneath would mean zero prompts and zero containment together. Re-verified live: a second sandboxed dispatch requiring real Bash execution succeeded. Dispatch pause restored to its prior state after testing — **`sandboxed_dispatch_enabled` was reported as reverted here too but actually wasn't; see the correction later in this same day's entry.** **Fixed the "Working Directly on the Omen via SSH" section above** — its documented step 3 ("commit and push from the Omen") doesn't actually work: the Omen's GitHub deploy key is read-only, confirmed live. Corrected to route the push through the Aero instead of widening the key's access, plus documented two more real gotchas hit along the way (no git identity configured on the Omen, non-interactive SSH missing `~/.local/bin` on PATH). **Filed 10 Controller-expansion tasks** (`86bb3cey0`–`86bb3cgna`, tagged `controller-expansion`) after Marvin asked for suggestions and said "I like all of them" — live status, approve/deny mechanisms, diff-merge, abort switch, optimistic-UI write queue, etc. **Shipped `86bb3cey0` same day (commit `4367d62`)** — Nova Controller live in-flight dispatch status widget, new `GET /in-flight-status`. Scoped to the headless-dispatch lane only (confirmed with Marvin during planning): the interactive/native `nova_orchestrator.py` lane only runs on the Aero, invisible to the Omen's `nova_api.py` (what the phone Controller actually hits) without a new push mechanism — deferred as a real, separate task. New `current_dispatch.json` marker in `nova_scheduled_dispatch.py`, written the moment a task is picked and cleared alongside the existing dispatch lock; `is_dispatch_currently_running()` reuses the lock's own PID-liveness check (`_pid_is_alive()`, extracted for reuse) so a crashed process self-heals the same way a stale lock already does. Investigated `tool_call_log.jsonl` as a live-detail signal first and rejected it: hook-tagged entries are all indistinguishable (`agent: "claude_cli"` regardless of source), and — a real, previously-undiscovered gap — **sandboxed dispatch's tool-call-log writes are silently lost entirely**, since the container only mounts `.git` + the worktree + `~/.claude`, not the repo's `logs/` dir; noted for a future fix, not addressed here. Widget is a persistent element alongside Board Watch/DPO-progress, not a Feed card, matching the Controller's existing anti-engagement-ranking design. Verified: 4 states of `is_dispatch_currently_running()` directly, the route against a local dev server, `formatElapsed()` unit-tested in Node, and one real end-to-end dispatch against a disposable test task. **Real self-caught error during that verification:** `sandboxed_dispatch_enabled` was still `true` — the earlier same-day claim that it had been reverted was wrong; fixed for real this time (commit `588fb93`), confirmed via direct file read after the fix rather than re-asserted from memory. **Shipped the Controller flag switches panel** (`86bb3d725`) — `GET`/`POST /flags`, `nova_config.FLAG_REGISTRY` (7 flags: dispatch_pause + 6 nova_config.json flags), replaces the standalone Board Watch widget with one unified panel. Real design correction found live: the toggle route originally also pushed nova_config.json to origin/master after committing, which can never succeed from the Omen (same read-only deploy key) — asked Marvin directly, confirmed keep the key read-only, toggle route now commits locally only, publishing stays a manual step like the rest of this file's history. Two more real bugs found and fixed during the same live verification, both specific to running inside the Omen's systemd service rather than an interactive shell: no git identity configured (fixed with scoped `-c` flags) and systemd's own minimal PATH missing `~/.local/bin` (fixed with an explicit subprocess `env`, not relying on the parent process's PATH) |

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
