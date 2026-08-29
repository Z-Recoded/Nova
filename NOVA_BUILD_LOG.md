# NOVA_BUILD_LOG.md — Full Build Narrative & Detailed Change Log

> Companion to `CLAUDE.md`. CLAUDE.md holds current facts, standards, and pointers into this
> file — this file holds the "why," the bug stories, the live-verification steps, and the
> full dated change log. Split out 2026-07-26 because CLAUDE.md's own coding-standards
> content (~18K chars) was being buried under ~136K chars of accumulated narrative (88% of
> the file). Same precedent as `omen_setup_runbook.md` — a standalone reference doc read on
> demand, not auto-loaded every session.
>
> Read this when you need the full story behind a decision, an incident, or a specific file's
> build history. For current architecture/status, read `CLAUDE.md` first.

---

## Part A — Per-File Build Notes (full narrative)

Full backstory for each file listed in CLAUDE.md Section 1's file table.

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
  block (see CLAUDE.md's Escalation Protocol subsection for the
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
  `/escalations` routes + `nova_escalations.html` UI — see CLAUDE.md's
  Escalation Protocol subsection and route table

- `nova_omen_sync.py` — one-command sync for the Omen's MAIN checkout
  (distinct from `nova_omen_dispatch.py`'s worktree path above, which
  already self-syncs by fetching fresh from origin every run). Collapses
  the sequence that caused the earlier 15-commit stale-clone incident
  (see Part B, "HP Omen Headless Server") into one call: `git pull` →
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

---

## Part B — Architecture Narrative (full detail)

### Phase Roadmap Detail (full paragraphs)

- **Phase 1.75 Retrieval Intelligence**: Backlog, build-first foundation laid — feature-flag system ✓ v1 live (2026-07-06, `nova_config.py`/`nova_config.json`, all flags off; `config_snapshot()` wired into every query's `query_log.jsonl` entry so flag state is tied to results before any augment exists). Remaining: A* graph traversal + document-level embeddings for the heuristic, DP context-window packing, priority-queue routing, two-tier memory decay, weighted wikilinks, link-aware ingestion upgrade — each will gate on a flag already defined above

- **Phase 2 Voice & Capture**: Explicitly greenlit early, in progress (`86baeyg3q`, confirmed with Marvin 2026-07-12) — Whisper Distil (STT) + Piper (TTS) + "Hey Nova" wake word + iPhone/Apple Watch quick-capture, target 2-4s full round-trip, explicit-consent-only (no passive public capture). This is a deliberate exception to the "don't build Phase 2+ without explicit instruction" rule, not a stale status — confirm with Marvin if picking this back up after a gap to make sure the exception still stands

- **Phase 2.5 Agent Layer**: Backlog — file CRUD ✓ v1 live (`nova_tools.py`); Nova MCP Server ✓ v1 live (2026-07-06, wraps `/ask` `/graph` `/neighbors` `/context-budget` `/ingest` as MCP tools over streamable-http:8100) but not yet wired to any MCP client or into `nova_orchestrator.py` itself; Docker sub-agent orchestration deferred, see Phase 3.5. **Browser Hands harness (M1) ✓ v1 live (2026-07-10, ClickUp `86barqzmv`):** `browser_hands/` package — CDP-attach-only browser automation foundation, generalized from `C:\Projects\developer_tools\base44_export.py`. No automated login, ever (hard rule). Adapters (M2-M5) are separate, still-backlog tasks. Writes to a new `browser_tasks` table in `nova_state.db`, separate from the generic `domain_state` table

- **Phase 3 First Fine-Tune**: Backlog — Unsloth + DPO → GGUF → Ollama (conversational/lore lane). Base-model re-eval DONE, verdict is stay on Llama 3.2 3B (2026-07-21). Swap trigger: `nova_benchmark.py --golden` established a real Llama 3.2 3B baseline; a candidate model must clearly beat that logged baseline before a swap. Model-swap eval wrapper ✓ v1 live (`nova_benchmark.py --evaluate <model>`). Dynamic model routing ✓ mechanism live (`nova_config.json`'s `model_routing`, default off, currently everything maps to llama3.2). Dual-model VRAM fit — NO-GO, confirmed empirically 2026-07-11 (`86bagek35`): qwen3:8b alone uses 86% of the Aero's 8GB card, loading phi4-mini alongside evicts it every time. Fine-tune pipeline re-scoped for Phi-4 Mini (`86bagf51n`, 2026-07-21): `nova_finetune_phi4.py` built, Unsloth QLoRA DPO script, trains on the Aero's RTX 5070 (required a CUDA `torch` reinstall — `cu128`, pinned `torch<2.11.0` for Unsloth compat). Verified live: real `--dry-run` loaded the quantized model, attached a real LoRA adapter, ran 3 real DPO training steps, loss dropped 0.693→0.414. `run()` hard-refuses below `MIN_REAL_PAIRS = 100` (currently only 11-33 real pairs). `86bagek35` closed out same day: context-fill/cold-start benchmarks added to `nova_benchmark.py`, real numbers on phi4-mini: 8K→7.4s, 32K→15.8s, 65K→58.2s, 128K→171.2s, cold start 4.3s. **Full base-model evaluation protocol run 2026-07-21 — verdict: stay on Llama 3.2 3B.** Ran every real candidate (llama3.2, llama3.1:8b, phi4-mini, qwen3:8b with think=False, gemma3:4b) through the golden-query suite fresh in one session. Results: llama3.2 (baseline) 3135ms; every other candidate FAILED (4251-7133ms). Llama 3.2 3B remains the fastest model on this actual RAG workload on this hardware — not a permanent verdict, just "no swap justified today."

- **Phase 3.5 Coding Agent Lane**: ✓ v1 live (2026-07-05) — Claude API-backed coding sub-agent (`nova_orchestrator.py`), git-worktree isolated, no Docker/OpenHands yet. Qwen3 8B swap trigger: ~30-50 diverse real task transcripts in `agent_log.jsonl`, ~20% held out as eval set, swap only once Qwen3 clears a defined pass bar against Claude's baseline on the same tasks. LangGraph orchestration v1 live (2026-07-10, `86bat0u81`): `nova_orchestrator_graph.py`, gated behind `framework_integrations.langgraph_orchestration` (default off). Verified: same trivial task through both paths produced identical results.

- **Phase 4 Roaming Layer**: ✓ Lightweight v1 shipped (2026-07-05) — Tailscale, Task Scheduler auto-start, two admin-elevated firewall rules for Tailscale's Private-profile classification. HP Omen headless Ubuntu server (`86baeyfm1`) — ✓ COMPLETE, verified 2026-07-12 (full story in Part B below). `nova_api.py` deployed to the Omen (`86bawfn19`) — ✓ COMPLETE, verified 2026-07-12, real test of done passed (reached Omen's `nova-api` over Tailscale with Aero fully powered off).

- **Phase 6 Domain Expansion**: Backlog, domain state layer foundation laid — `nova_state.db` schema + system adapter ✓ v1 live (2026-07-07); financial/work/creative/games adapters and the alert engine remain blocked on real open questions. Chunk visualization tool (`86bara3tj`) — CLI stage ✓ v1 live. Embedding-space visualization (`86bawjg14`) ✓ v1 live.

### HP Omen Headless Server (ClickUp `86baeyfm1`) — ✓ COMPLETE (2026-07-12)
Repurposed the HP Omen as an always-on Ubuntu service host for Chroma, `nova_state.db`, and
orchestration — replacing the Aero (which sleeps) for those specifically. Confirmed service-
host-only, not a model-inference host: its GTX 1050 Ti (4GB, Pascal) can't run the planned
dual-model routing. Full step-by-step commands live in `omen_setup_runbook.md` (all 13 phases,
0-12, marked done).

Live and verified end-to-end: Ubuntu 24.04 installed (static IP `192.168.1.250` on `eno1`),
Chroma migrated from `PersistentClient` to `HttpClient` and running as a standalone server there
(port 8000), Chroma data transferred from the Aero, lid-close set to ignore, SSH from the Aero
via key-based auth, `nova-chroma` and `nova-api` running as permanent systemd units, `ufw`
tightened to LAN-subnet-only, Tailscale live on the Omen (tailnet IP `100.114.197.117`, hostname
`nova`). `nova-api` runs on port 8001 on the Omen (not 8000) because both services defaulted to
8000 and conflicted once co-located on the same box.

Final validation, all confirmed live: `nova_chroma_omen_check.py` full pass against both the
LAN IP and the Tailscale IP; `nova_api.py` returns 200 on `/`, `/headroom`, `/docs` on both IPs
(`/graph` correctly 404s); Ollama callback path confirmed working in both directions, including
a `curl` run from the Omen's own shell reaching the Aero's Ollama over the tailnet.

Real bugs found and fixed along the way: a second physical disk still had the old Windows
install (wiped and reclaimed); a nested-folder flatten; a UTF-16-encoded `requirements.txt`
(PowerShell default output encoding artifact); `pywin32` (Windows-only) needed stripping from
the cloned repo/venv on Ubuntu; the Chroma/`nova-api` port conflict; `nova_orchestrator.py`'s
`load_dotenv(dotenv_path="C:/Nova/.env")` was hardcoded to a Windows path — broke silently on
Linux — fixed to resolve relative to the script's own location.

**Board hygiene note:** the runbook itself claimed SSH access into the Omen "closes out ClickUp
`86bavtz06`" — checked the actual task and it's really "Onboard Nova server, Pi fleet, and
trading bot box via SSH," three targets, only one done. Moved to "in progress," not "complete."

### Important gap in the "COMPLETE" verification above, found and fixed same day (`86bawfn19`)
The verification that marked `86baeyfm1` complete checked reachability (200 on `/`, `/headroom`,
`/docs`) but never actually exercised `/ask`'s real RAG behavior on the Omen's own deployment.
Picking up `86bawfn19` surfaced a real, serious gap:

1. **The Omen's git clone was 15 commits stale** — still on `5146222`, missing `b5f7f68` (the
   actual `PersistentClient` → `HttpClient` migration) and everything after it. `nova_query.py`
   there was still the old code, silently defaulting to an empty local Chroma store instead of
   the real `nova-chroma` server — `collection.count()` was 0, and `/ask` returned fluent but
   completely hallucinated answers (e.g. claimed Null "was the lead singer of a fictional
   industrial rock band called Riven" — not in the corpus at all) with empty `sources`/`chunks`,
   no error, no signal anything was wrong except the content being wrong.
2. **Root cause: the GitHub deploy key (`id_ed25519_github`) was passphrase-protected**, which
   silently breaks any unattended `git pull`/`fetch` — no TTY to prompt for it. Fixed by
   regenerating the key with no passphrase.
3. Once fetchable, `git pull` brought the Omen fully current (`5146222` → `01b0866`, 14 files).
   `requirements.txt` needed `pywin32` filtered out before `pip install`.
4. **A second hardcoded-Windows-path bug**: `nova_api.py`'s `GRAPH_PATH = "C:/Nova/nova_graph.json"`
   silently returned empty nodes/edges on Linux instead of erroring. Fixed to resolve relative
   to the script's own location.

**Lesson:** "reachable" and "functionally correct" are different claims — a route returning 200
doesn't mean it's doing real work. Verify the payload, not just the status code, especially for
routes that can fail open (wrong data, not an error) rather than fail loud.

**`86bawfn19` — COMPLETE.** All 4 scope items done: requirements.txt encoding, `nova-api`
running as a systemd unit (port 8001, enabled, survives reboots), the Ollama-callback path
confirmed both directions, and the real test of done — reaching the Omen's `nova-api` over
Tailscale with the Aero fully powered off, getting a real grounded answer back.

### Omen Capacity Audit (86baxty6d, self-hosting gate) — 2026-07-21
This task exists because every self-hosting decision so far (Chroma, Ollama callback,
Dockerized services, headless dispatch, the still-unscoped Langfuse/Vaultwarden/self-hosted-git/
Obsidian-CouchDB-sync ideas) was scoped individually, assuming "the Omen can host this," with
nobody ever checking the sum — flagged 2026-07-13 as a gate: no further self-hosting tasks
proceed until this audit happens, revisited periodically as new services get proposed.

Built `nova_omen_capacity.py` — SSHes from the Aero and pulls a real CPU/RAM/disk/GPU snapshot
plus what's actually running. Appends one line per run to `logs/omen_capacity_log.jsonl`.

**Real findings, run live 2026-07-21:**
- Compute headroom is large: 8 CPU cores at near-zero load (0.04 avg), 6.42GB of 7.64GB RAM
  available (84% free), 75.4GB of 97.9GB disk available (77% free), swap barely touched.
- Because almost nothing is actually deployed yet — only `nova-api.service` and
  `nova-chroma.service` are persistent. Every other self-hosting idea is still `[Initiative —
  not scoped]`. Docker is installed and running but completely empty (0 containers, 0 images).
- Disk breakdown: Chroma's real data directory is only 0.02GB, the git repo is negligible, the
  one real disk consumer is the Python venv itself (5.53GB, fixed-size).
- GPU confirmed present but unusable: a real GTX 1050 Ti Mobile (GP107M) — but zero NVIDIA
  driver is installed, so it's not a factor in capacity planning at all right now.

**Verdict: gate open for today's actual headroom, not a blanket clearance for everything
queued.** Recommendation, not yet acted on: re-run `nova_omen_capacity.py` before and after each
individual self-hosting task actually gets deployed, watching RAM specifically — it's the
smallest absolute pool of the three and the one a multi-service database stack (Langfuse's
Postgres+ClickHouse+Redis) would plausibly pressure first.

### Nova Coding Sub-Agent (nova_orchestrator.py) — backstory
Nova can now write to its own codebase — the one sanctioned exception to a human surfacing
every change before it's applied. Safety comes from git worktree isolation, not manual review
of each write: every task runs in its own disposable worktree + branch under
`C:/nova-agent-worktrees/`, never the live `C:/Nova` tree. `nova_orchestrator.py` never merges
or deletes a worktree — Marvin always reviews the diff and merges by hand. v1 is driven by the
Claude API (not a local model yet) and has no Docker/OpenHands sandboxing yet — see Phase 3.5.

LangGraph orchestration (2026-07-10): the turn loop inside `nova_orchestrator.py` can now run
via `nova_orchestrator_graph.py` (LangGraph nodes/edges) instead of the original inline loop,
gated behind `framework_integrations.langgraph_orchestration` (default off) — see Phase 3.5 and
ClickUp `86bat0u81`.

### Escalation Protocol — decision narrative (86bax0wkj, 2026-07-18)
See CLAUDE.md Section 2 for the current block format and route table. Full backstory:

**Mechanism, end to end:** `nova_escalation.check_escalation()` parses the block out of the
dispatch/resume result's own summary text via regex. `nova_scheduled_dispatch.py`'s
`_handle_escalation()` registers it with `nova_api.py`'s `POST /escalations` (never a direct
`nova_state.py` import — see that file's `pending_escalations` entity note: the same
cross-machine hardcoded-`DB_PATH` bug that already broke `dispatch_pause`), tags the ClickUp
task `awaiting-answer`, and comments the question. Marvin answers via `GET /escalations-ui`
(`nova_escalations.html`); the answer is accepted immediately (fire-and-forget `BackgroundTasks`),
and `nova_omen_dispatch.resume_headless_task()` runs `claude -p --resume <session_id>` in the
background, `cd`'d into the exact original worktree.

**Four decisions confirmed with Marvin:** (1) resuming an escalated session is not blocked by
the global dispatch-pause switch — answering a direct question is a different act than a new
autonomous run starting while he's mid-build; (2) agent-log transcript ingestion is now genuinely
idempotent via a per-session turn cursor (`logs/agent_log_ingest_cursor.json` on the Omen) — a
resumed session's earlier turns must not duplicate into the Qwen3 training corpus; (3)
`POST /escalations/{id}/answer` requires header `X-Nova-Escalation-Token` matching env var
`NOVA_ESCALATION_TOKEN` — the first cost-incurring write route on `nova_api.py`'s otherwise-
unauthenticated Tailscale-only surface, ahead of the general token-auth ticket (`86bawf2z2`);
(4) a task awaiting an answer gets ClickUp tag `awaiting-answer`, not just a comment.

**Review-backpressure interaction (found and fixed same day):** a paused-for-escalation
dispatch has a real `session_id` but `success` is not `True` — without a fix, it would count
against both the review-backpressure cap (a "not done yet" task counted as "done and
unreviewed") and fire a non-clean-outcome ClickUp comment alongside the escalation comment.
Fixed via `handle_dispatch_outcome()` checking escalation first (mutually exclusive `if`/`elif`
with the clean/non-clean branch) and `count_unreviewed_dispatches()` excluding
`pending`/`resuming` task_ids via new `_pending_escalation_task_ids()` (fails toward an
empty/non-excluding set on error, keeping the cap conservative).

**Manual step required:** Marvin must set `NOVA_ESCALATION_TOKEN` in the Omen's `.env` and
restart `nova-api` before the answer route will accept anything — it 401s otherwise, by design.

### Task Tiering (86bb01wur, 2026-07-19)
Extends `nova_task_queue.py`'s existing `autonomy-safe` batch-tag gating with a per-task decision
made at creation/rescope time instead of a later sweep. Nova proposes an autonomy tier
(`autonomous` / `needs review` / `manual only`) + a qualitative confidence (`low`/`medium`/`high`)
+ one-sentence reasoning via `propose_tier()` — a single non-agentic Claude completion, not the
full agent loop. Detection is polling-based inside `nova_scheduled_dispatch.py`'s existing
2-hour loop (`nova_task_queue.detect_tier_candidates()` diffs each task's real `date_updated`
against a stored per-task watermark) — no ClickUp webhooks exist anywhere in this codebase.

Reuses `86bax0wkj`'s exact propose→register→notify→answer shape: `system/pending_tier_proposals`
+ `system/task_tier_watermarks` in `nova_state.db`, new `/tier-proposals`/`/tier-watermarks`
routes, a decide route reusing the same `X-Nova-Escalation-Token`. The `autonomous` tier maps to
the exact existing `autonomy-safe` tag string (`TIER_TAGS`) — `get_practice_queue_tasks()`
needed zero code change.

Only plausibly-dispatchable tasks get tiered — exploratory `"Spec:"`-prefixed tasks are skipped
(`_is_tierable()`). A `--sweep-tiers [--limit N]` CLI flag on `nova_task_queue.py` does the
retroactive backlog backfill.

**Two real bugs found and fixed during live verification:** (1) `detect_tier_candidates()`
originally persisted the watermark map as a side effect of merely being called — a pure
inspection call with no intent to process anything silently marked the whole backlog "seen,"
which would have quietly defeated the retroactive sweep before it ever ran. Fixed by splitting
detection (pure read/diff) from `persist_tier_watermarks()` (explicit, called only after a
caller has actually attempted every candidate). (2) `propose_tier()` didn't strip markdown code
fences from Claude's response — Claude sometimes wraps its JSON in ```json ... ``` despite being
told not to, which silently tripped the fail-toward-restrictive fallback on a real proposal.
Fixed to strip a leading/trailing fence before parsing.

### Nova Controller UX (86baxahn7, 2026-07-19)
The real UX layer on top of `86bax0wkj`'s backend — one reverse-chronological Feed
(`nova_controller.html`, served at `GET /controller`) replacing separate dashboards, per
Marvin's 2026-07-13 framing (lift interaction primitives from social media, explicitly reject
engagement-optimization mechanics — no unread badges, no streak-as-pressure UI, no
engagement-ranked ordering; strictly chronological sort). `/escalations-ui` now redirects to
`/controller`; `nova_escalations.html` itself was retired, its escalation/tier-proposal card
logic ported in as-is.

Scoped to real data only: the Feed merges escalations, tier proposals, dispatch outcomes
(`GET /dispatch-log`), and tool-call/blend-flag swipe-labeling prompts (`GET /label-queue`).
Tutor-prompt and differential-scorer card types are not built — no `nova_tutor*.py` or
`nova_differential*.py` file exists anywhere, both are pure ClickUp backlog.

The swipe-labeling cards are the real UX target — `was_necessary`/`was_used` and `correction`
have sat `null`/`""` waiting for exactly this kind of judge-pass. Hand-rolled
`touchstart`/`touchmove`/`touchend` gestures, no gesture library since nothing in this repo's
frontend uses a bundler/npm. `POST /label-queue/{kind}/{id}/decide` (token-gated) patches the
matching `tool_call_log.jsonl`/`training_flags.jsonl` entry in place — a known, accepted
concurrency limitation is documented directly in the route's own docstring.

**PWA**: `manifest.json` + two hand-written flat PNG icons (generated via raw `zlib`/`struct` —
no Pillow) + `sw.js`, a service worker caching only the app shell, never live data. Verified
live: real browser load at phone width, zero console errors, a real label decision confirmed on
disk over SSH after clicking the button in the actual browser.

**Token Budget Governor — scoped v1 (2026-07-07, ClickUp `86barhqt9`):** the finalized spec
assumes infrastructure that doesn't exist yet — a push-notification channel, a ClickUp-driven
task queue with Sonnet/Haiku routing. Built `nova_token_budget.py` tracking the coding
sub-agent's Claude API consumption against `nova_config.json`'s `token_budget` thresholds,
persisted to `logs/token_budget_state.json`. Classifies normal/conservative/critical/halt and
folds into `GET /headroom`. `nova_orchestrator.py` checks the mode at the top of each turn loop
and stops cleanly once halted. Gated behind `token_budget_governor` (default off). Explicitly
deferred: Haiku downgrade, task-queue priority-aware selection, Open WebUI push notifications,
automatic ClickUp status updates on halt.

### Training-Data Accumulation Oversight (86bax4akx, 2026-07-21)
Closes the gap named 2026-07-13: existing tasks build or consume training sets but nothing
tracked accumulation as it happens.

**Real spec-vs-repo check before building:** two of the ticket's five scope items don't have a
real data source to build against — a "tutor-domain `blend_flag` log" (Nova Tutor is entirely
unbuilt) and a "coding"-domain coverage bucket (`86bara7pn` is still blocked, zero coding DPO
pairs exist). Built what's real instead of stubbing those two.

**Built:** `GET /training-data-status` — live count from `logs/training_flags.jsonl` computed
fresh on every call. New `dpo_verify` kind on the existing `/label-queue` mechanism — the real
three-state shape (unverified → confirmed-good / needs-rework). `nova_controller.html` DPO-pairs
progress-bar widget.

**Real bug found and fixed during live verification:** testing `/label-queue` at its real
default `limit=50` showed zero `blend_flag`/`dpo_verify` entries — `tool_call` entries are so
much more numerous (2400+) that a single merge-then-truncate silently starved out every
training-data card. Fixed by capping each kind at `limit` independently before merging.

**Real incident, same day:** Marvin loaded `/controller` on the Omen and saw 0/100, not 11/100.
Root cause: `nova_logger.py`'s `LOGS_DIR` and `nova_corrector.py`'s `JSONL_PATH`/dotenv path
were both still hardcoded to `"C:/Nova/..."`. Confirmed via SSH: the Omen has no
`training_flags.jsonl` file at all. Fixed both to resolve relative to their own file location.

**Broader pattern check, same incident:** grepped the whole codebase for `C:/Nova` — found 17
more real instances beyond the ~8 already fixed, filed as dedicated task `86bb1pkpb` for
per-file review (two of the seventeen need a different fix shape than a relative-path swap).

### Nova Skills Library (2026-07-07, ClickUp `86barguac`)
Structured per-category instruction files (`skills/coding.md`, `retrieval.md`, `financial.md`,
`orchestration.md`, `lore.md`, `memory.md`) that `nova_orchestrator.py` can prepend to a coding
task's context. `load_skill()`/`get_skill_version()` live in `nova_skills.py`; a missing category
or skill file is a graceful no-op. Each turn's `agent_log.jsonl` entry carries
`skill_category`/`skill_version`. Gated behind `skill_injection` in `nova_config.json` (default
off). One trim from the literal spec: category comes from an explicit caller-supplied string
(`run_coding_task(task, category=None)`), not a ClickUp tag, since nothing in Nova's own runtime
reads ClickUp today.

### Domain State Layer (2026-07-07, ClickUp `86bara3qe`) — scoped v1
Architecture Principles v1.1, Principle 6 distinguishes Chroma (deep knowledge) from
`nova_state.db` (current reality), and defines 5 domains × 12 entities. Built `nova_state.py` —
one generic `domain_state` table (`domain`, `entity`, `data` JSON, `updated_at`) rather than
fixed per-entity columns invented ahead of real data. `write_state`/`get_state`/`get_domain` are
the only interface. Also built `nova_state_system.py`, wrapping `nova_headroom.get_headroom_report()`
into `system/nova_health` and `system/pending_alerts`. Explicitly deferred, each on a real open
question: `nova_state_financial.py` (no approved data source), `nova_state_work.py`/
`nova_state_games.py` (no ClickUp API access inside Nova's own runtime code),
`nova_state_creative.py` (no art-practice-log data source found). No alert engine, no refresh
scheduler yet either.

---

## Part C — Full Dated Change Log

Full narrative detail for every change — bug stories, live-verification steps — behind
CLAUDE.md's terse one-line index.

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
| 2026-07-25 | Shipped `nova_log_rotation.py` (`86barby7t`, merged PR #9) — weekly, non-destructive rotation of the Nova Log telemetry files (`query_log.jsonl`/`benchmark_log.jsonl`): archives entries >90 days old and past the most-recent 1000 into month-stamped files under `logs/archive/`, atomic active-file rewrite. Standalone cron CLI (`--dry-run`/`--file`/`--max-age-days`/`--max-active`), not wired into the deferred `nova_watcher.py`. Deliberately scoped to the two real Nova Log files only — the other append-only JSONL logs feed full-history consumers and need a per-log decision before rotating. Live `--dry-run` against the real Aero logs confirmed clean before merge (158/12 entries, both unchanged, no errors). **Merged PR #8** — closed the `run_command()` absolute-path-argument gap (`_command_references_outside_root()`), independently re-verified with real test cases before merging. **Real end-to-end sandboxed-cron-dispatch test run for real** — surfaced and fixed a serious bug: `--permission-mode acceptEdits` never approves the Bash tool, only file edits, confirmed via a real `permission_denials` entry on a bare `python3 -c "print(2+2)"` call outside Docker entirely. This affected both headless dispatch paths since 2026-07-14, unnoticed because no prior real dispatch had needed to execute code. Fixed by switching only the sandboxed path to `--permission-mode bypassPermissions` (safe there specifically because the Docker mount boundary is real, verified containment) — deliberately left the bare-SSH path alone. Re-verified live: a second sandboxed dispatch requiring real Bash execution succeeded. Dispatch pause restored to its prior state after testing — **`sandboxed_dispatch_enabled` was reported as reverted here too but actually wasn't; see the correction later in this same day's entry.** **Fixed the "Working Directly on the Omen via SSH" section** — its documented step 3 ("commit and push from the Omen") doesn't actually work: the Omen's GitHub deploy key is read-only, confirmed live. Corrected to route the push through the Aero, plus documented two more real gotchas (no git identity configured on the Omen, non-interactive SSH missing `~/.local/bin` on PATH). **Filed 10 Controller-expansion tasks** (`86bb3cey0`–`86bb3cgna`, tagged `controller-expansion`) after Marvin asked for suggestions and said "I like all of them." **Shipped `86bb3cey0` same day (commit `4367d62`)** — Nova Controller live in-flight dispatch status widget, new `GET /in-flight-status`. Scoped to the headless-dispatch lane only. New `current_dispatch.json` marker; `is_dispatch_currently_running()` reuses the lock's own PID-liveness check (`_pid_is_alive()`, extracted for reuse). Investigated `tool_call_log.jsonl` as a live-detail signal first and rejected it: a real, previously-undiscovered gap — sandboxed dispatch's tool-call-log writes are silently lost entirely, since the container only mounts `.git` + the worktree + `~/.claude`, not the repo's `logs/` dir; noted for a future fix. **Real self-caught error during that verification:** `sandboxed_dispatch_enabled` was still `true` — the earlier same-day claim that it had been reverted was wrong; fixed for real this time (commit `588fb93`), confirmed via direct file read rather than re-asserted from memory. **Shipped the Controller flag switches panel** (`86bb3d725`) — `GET`/`POST /flags`, `nova_config.FLAG_REGISTRY` (7 flags), replaces the standalone Board Watch widget with one unified panel. Real design correction found live: the toggle route originally also pushed nova_config.json to origin/master after committing, which can never succeed from the Omen (read-only deploy key) — asked Marvin directly, confirmed keep the key read-only, toggle route now commits locally only. Two more real bugs found and fixed: no git identity configured on the Omen's systemd service, and systemd's own minimal PATH missing `~/.local/bin`. **Shipped `86bb3ceya`** — Nova Controller headless-dispatch cost/budget readout, new `GET /dispatch-cost-summary`. Real scope correction: `nova_token_budget.py` tracks the interactive `nova_orchestrator.py` lane only, headless dispatch never calls its `record_usage()` at all. Built the honest half instead: `get_dispatch_cost_summary()` sums `cost_usd` from `scheduled_dispatch_log.jsonl`, bucketed today/last-7-days, split into `real_usd` (`fuel_source == "api_key"` only) vs. `notional_usd` (every entry) — deliberately never collapsed into one misreadable number. Marvin's follow-up: split the real/notional line into two separate lines after seeing it, shipped same session (commit `5957698`). **Shipped `86bb3cey2`** — Nova Controller Qwen3 8B swap-trigger progress widget, new `GET /qwen-swap-status`. Real architecture problem found before writing code: `nova_agent_log_status.py` was built assuming "local = the Aero, fetch the Omen's copy over SSH" — false once wrapped in a `nova_api.py` route that also runs on the Omen. Fixed `LOCAL_AGENT_LOG_PATH`'s hardcoded `C:/Nova/...` path and made `get_combined_status()` platform-aware. Verified live on the Aero: real combined count is 29 distinct task_slugs (27 Aero + 2 Omen), 96.7% of the 30-task swap-trigger minimum. Also verified `view: "omen_only"` for real on the live Omen-hosted `nova-api`: real numbers there are 2/30 (6.7%). **Shipped `86bb3cey5`** — Nova Controller Feed filtering by card type/task/date, `nova_controller.html` only. Client-side filter bar above the Feed; `loadFeed()` split into fetch/merge and a new `renderFeedEntries()` that filters a cache of the last fetch, never changes what's fetched or re-ranks anything. **Shipped `86bb3ceyc`** — Nova Controller worktree browser, new module `nova_worktree_status.py` + `GET /worktree-status`. Real motivating find: two stale `sandbox-verify-test*` worktrees from an earlier Docker-sandboxing PR verification were sitting unmerged on the Omen, unnoticed until manually checked. Same platform-awareness pattern from `86bb3cey2`. Real, useful finding confirmed live on first run: both stale worktrees are `merged: true` — already safely mergeable into `master` and just never pruned. Same day, Marvin asked to move the Worktrees and Discover sections above the Feed, shipped same session (commit `4b92ed9`). **Shipped `86bb3cgna`** — optimistic UI + serialized write queue for label-queue swipe/tap decisions, `nova_controller.html` only. Real, measured gap fixed: every swipe/tap previously awaited the full round trip then refetched all 4 Feed endpoints against a `tool_call_log.jsonl` already past 2400 entries. Now: the card collapses immediately, a ~4s Gmail-style undo snackbar holds the actual write, and only once that window elapses uncancelled does the write get pushed onto a client-side queue that processes exactly one at a time. Real correctness problem found while tracing the code: the Feed's own 8s poll rebuilds `#feed`'s entire `innerHTML` from a fresh fetch every cycle — fixed with a `suppressedLabelIds` set the render filter also excludes. Verified via a standalone Node reproduction of the exact `enqueueWrite`/`processQueue` pattern, run against 5 rapid-fire simulated writes with randomized latency, confirmed strictly one-at-a-time processing. **Chrome connected later the same session** — real live verification followed using direct `javascript_tool` execution against the page's own functions. Confirmed correct end to end: immediate collapse, correct undo-snackbar text, write genuinely deferred, undo cleanly cancels, suppression survives across time, failure toast + tap-to-retry both fire correctly. Real, separate finding: `/training-data-status` shows 0/100 on the live Omen vs. the real 33/100 on the Aero — same "Omen can't see Aero-only data" bug class fixed three times today for newer routes, just never applied to this older one. Flagged, not fixed. **Extended to tier proposals same day** — generalized `suppressedLabelIds`→`suppressedCardIds` and `optimisticallyCommitLabel`→`optimisticallyCommitCard`, wired `renderTierCard()`'s Accept and Submit-override buttons through the same path. **Also same session: prepared (not yet completed) command-restricted Omen→Aero SSH access.** Two dedicated, narrowly-scoped ed25519 keypairs generated on the Omen (`aero_agentlog`, `aero_worktrees`); two forced-command scripts written and verified working locally; one idempotent elevated-PowerShell setup script prepared. Real tradeoff surfaced and discussed: this gives the Omen a genuine remote-execution foothold into the Aero — mitigated by scoping each key to exactly one whitelisted read-only script, no shell access. **SSH setup completed live the same session, two real bugs hit and fixed:** (1) `Add-WindowsCapability -Online` for OpenSSH Server hung indefinitely — root cause was Windows Update service itself being stuck, fixed by `Restart-Service wuauserv -Force`. (2) The setup script's `ListenAddress` restriction step blindly appended the directive to the end of `sshd_config`, landing it after Windows' default `Match Group administrators` block — `ListenAddress` isn't in the small allowlist of directives OpenSSH permits inside a `Match` block, so `sshd` refused to parse the config. Fixed both the live config and the setup script. **Real end-to-end connectivity verified directly over SSH from the Omen**, both dedicated keys confirmed working. **Same session, wired into the Python side**: `nova_agent_log_status.get_combined_status()` and `nova_worktree_status.get_worktree_status()` both refactored to a fully symmetric `local` + live-SSH-to-`remote` shape — `view` is now genuinely three real states either module can return: `"combined"`, `"omen_only"`, or `"aero_only"`. Verified live end-to-end through the real deployed routes after syncing: both `/qwen-swap-status` and `/worktree-status` returned `"view": "combined"` with genuine Aero task/turn counts, served from the Omen, for the first time |
| 2026-07-26 | Applied the same Omen→Aero SSH bridge to `/training-data-status` — new `nova_training_data_status.py` (`get_combined_training_status()`, identical `combined`/`omen_only`/`aero_only` shape), a third dedicated command-restricted key (`id_ed25519_aero_trainingdata`) + forced script, and `setup_omen_to_aero_ssh.ps1` updated to install it alongside the existing two. Fixes the real gap flagged the day before: the route used to read only its own machine's local `training_flags.jsonl` — real on the Aero (33/100) but always 0/100 from the Omen. `nova_controller.html`'s DPO-progress widget now surfaces the same `omen_only`/`aero_only` caveat text as the Qwen widget. Verified live: the new module's local-read path confirmed against the real Aero `training_flags.jsonl` (33 corrected, all `fiction`, all unverified), and the pre-existing Aero→Omen leg confirmed reachable with an honest empty result. **Key generated on the Omen, activation still manual** — Marvin needs to `git pull` this change, then re-run `scripts/setup_omen_to_aero_ssh.ps1` (elevated) to install the third key and restart `sshd`. **Same-day follow-up: the write side.** `/label-queue`'s blend_flag/dpo_verify cards had the identical gap one layer deeper — when served from the Omen, the route could only ever see (and patch) the Omen's own near-empty `training_flags.jsonl`. Fixed with the same bridge, extended: new `nova_training_flags_patch.py` (shared `patch_training_flags_entry()`), `nova_patch_training_flags_cli.py` (a stdin/stdout JSON wrapper so a patch request can cross the SSH bridge as data), and a fourth, WRITE-capable key + forced script. `get_training_flags_by_origin()` now tags each entry's synthetic id with which machine it lives on; `decide_label_queue_entry()` patches locally or calls `dispatch_remote_patch()`. **This step is qualitatively bigger than the three read-only keys, and the session's own permission classifier caught that live** — attempting to `ssh-keygen` the fourth keypair on the Omen was blocked outright ("blocked by classifier"), a real, automatic guard against autonomously minting a new write-capable remote credential. Correctly treated as a stop-and-ask boundary: the code is fully built and unit-tested end-to-end against a disposable synthetic `training_flags.jsonl` (success path, stale-timestamp 409, bad-`verification_status` 422, malformed-JSON 422), and `setup_omen_to_aero_ssh.ps1` extended with an explicit opt-in step 6 requiring the real pubkey to be pasted in by hand. The keypair itself does not exist yet — generating it is left as a decision for Marvin. |
| 2026-07-26 | Shipped `86bb3ceyj` — Nova Controller abort/kill switch for an in-flight headless dispatch, first of the four Phase C Controller-expansion tasks. Scoped in a dedicated conversation first (4 settled decisions, recorded as a ClickUp comment): cron-fired dispatch only; the aborted task's worktree is left in place for manual review; a dispatch paused on a pending escalation gets a different action (cancel the escalation, not a process kill); the write is token-gated. **Real technical finding that shaped the whole build:** `_run_claude_over_ssh()` allocates no pty, so killing the wrapper process does NOT reliably kill the remote `claude -p` process — it just orphans it. Fixed by having the dispatch itself capture the real PID: `dispatch_headless_task()`'s remote_command now backgrounds the `claude` invocation and writes its real PID to a fixed path before `wait`ing on it, and `dispatch_headless_task_sandboxed()`'s `docker run` now takes a fixed `--name` so the sandboxed path can be killed with a plain `docker kill`. New `nova_scheduled_dispatch.abort_current_dispatch()` reads the in-flight marker, posts an immediate ClickUp comment naming the manual abort, then kills the real target (SIGTERM, escalating to SIGKILL after a 2s grace period; `docker kill` for sandboxed) — deliberately does NOT touch the lock/marker files itself. New routes: `POST /dispatch-abort` (token-gated) and `POST /escalations/{id}/cancel` (token-gated). Verified for real: `abort_current_dispatch()`'s full control-flow tested locally against real disposable subprocesses (not-running, no-PID-file-yet, malformed-PID, already-gone-process, and a real spawned Python process actually killed and confirmed dead); the actual SIGTERM-kills-a-real-process assumption was separately confirmed live over SSH against the real Omen. **Known, honestly-stated gap:** the two new routes weren't exercised through a live `TestClient`/dev server — a cold `import nova_api` hung past 60s in this environment — so route-level verification rests on exact structural parity with already-proven routes in the same file. |
| 2026-07-26 | Shipped `86bb3ceyf` — Nova Controller diff-preview-and-merge for dispatched tasks, second of the four Phase C Controller-expansion tasks. Scoped in a dedicated conversation first, starting from two real findings that reframed the whole approach: (1) the Omen genuinely cannot push to GitHub at all — its deploy key is read-only and it has no `gh` CLI installed — so a tap-to-merge action served from the Omen needs the Aero involved somehow; (2) GitHub's own mobile web UI already does diff review + merge well, so building a custom diff viewer would just be reinventing that. Settled: extend the Omen→Aero SSH bridge with a fifth key; push a real draft GitHub PR and have the Controller deep-link to it; scope to Omen-hosted headless-dispatch worktrees only; ship a discard action alongside merge, reusing `record_dispatch_review()`. **A second real technical finding, directly extending `86bb3ceyj`'s own discovery the same day:** confirmed the "SSH sessions here can't spawn a process" restriction isn't python.exe-specific — a generic Win32 `CreateProcess`-level "Access is denied," so it applies to `git.exe`/`gh.exe` too. That ruled out having the fifth key's forced script run git/gh directly; instead `scripts/ssh_relay_worktree_pr.ps1` only relays the request (via `Invoke-RestMethod`, a native .NET HTTP call, not a spawned process) to the Aero's own already-running `nova_api.py` instance, which does the real `git fetch`/`git push`/`gh pr create` work directly. Real, honestly-stated consequence: this only works when `nova_api.py` is actually running on the Aero. New `nova_worktree_pr.py` owns the git/gh logic and platform-aware relay decision; new routes `POST /worktree-pr` and `POST /worktree-discard` (both token-gated); new "Create PR"/"Discard" buttons on the worktree browser. **Verified:** branch-name validation and the full local git/gh success/failure control-flow tested with mocked subprocess calls; the relay script tested end-to-end via a real child-process stdin pipe, including a genuine connection-refused case against a real absent `nova_api.py`, which surfaced an unhelpful generic PS 5.1 exception message on the first pass — fixed to detect that specific case and report "nova_api.py may not be running on the Aero" instead. Same honest gap as `86bb3ceyj`: the two new routes weren't exercised through a live `TestClient`, and no real end-to-end PR/discard was run against an actual dispatch worktree this session. The fifth keypair itself was not generated — same stop-and-ask boundary as the fourth key, left for Marvin. |
| 2026-08-01 | Three real training runs on rented RunPod hardware, closing out `86baf4e70` Pattern 1. **First run (DPO warm-start):** verified live end to end — pod launch → SSH → deploy-key clone → a real SFT checkpoint already existed on the Hub from an earlier session (confirmed with Marvin, DPO warm-started from it) → DPO dry-run passed → real DPO run (5 corrected pairs, loss 0.693→0.594, reward accuracy 0.8) → merged export → uploaded to `zrecoded/nova-qwen-coder-32b-dpo-merged` (65.5GB, verified). Found and fixed five real gotchas: `requirements.txt`'s Aero-only pins don't install on Linux; HF cache defaults to the small container disk, not the volume; merging needs ~2x checkpoint size on disk at once (`DEFAULT_VOLUME_GB` bumped 100→200 in `nova_runpod_pod_launch.py`); HF private-storage plan limit hit with two ~65GB checkpoints at once, resolved by deleting the superseded SFT checkpoint; `logs/coding_review_log.jsonl` is gitignored so never reaches a fresh clone, silently zeroing out Nova's own training examples. Also confirmed downloading a checkpoint this size to the Omen/Aero over home internet is impractical (~5MB/s, ~3.4hr for 64GB) versus HF's own infrastructure (~670MB/s) — fix Hub-side blockers rather than routing around them. **Second run (real, non-warm-started SFT + DPO on its own output):** SFT ran 1 full epoch (~5hr GPU time, ~2,500 steps), loss →~0.15-0.20, merged and uploaded to `zrecoded/nova-qwen-coder-32b-sft-merged`; DPO (5 real corrected pairs) loss 0.69→0.67, reward accuracy 0.6, merged and uploaded to `zrecoded/nova-qwen-coder-32b-dpo-merged`. Two real bugs found: the DPO merge step silently died with no traceback — root-caused to **disk-quota exhaustion** on the 200GB network volume (multiple large outputs landing on the same quota simultaneously), fixed by deleting the stale partial output and symlinking the DPO output to the separate container-disk quota; and `nohup`/`disown`/`setsid` all stopped reliably detaching any backgrounded process on the pod (confirmed via direct process checks) — worked around by keeping the SSH session open in the foreground rather than a real fix, flagged as a gap for the next long run. **Third: AWQ quantize + redeploy + re-eval.** New `nova_quantize_qwen_coder_awq.py` (via `llm-compressor`, uploaded to `zrecoded/nova-qwen-coder-32b-awq`, 19.3GB) and `nova_runpod_endpoint_deploy.py` (new serverless endpoint, not a mutation of production, so rollback stays a two-constant edit). Three real bugs found and fixed: `autoawq` is deprecated, switched to `llm-compressor` (the initially-assumed "latest" 0.6.0.1 was six minors behind and hit a real tokenizer `AttributeError`, 0.12.0.1 fixed it); the documented 512-sample/2048-token calibration defaults OOM'd a fresh 80GB A100 outright (`llm-compressor`'s AWQ grid-search caches per-layer activations for the whole batch at once, not streamed — dropped to 128/1024, completed cleanly); the new endpoint 403'd on every invoke despite correct config, root cause was a **checkpoint-format mismatch** (`llm-compressor` saves `compressed-tensors` format, not the older `awq` format the production model's template `QUANTIZATION=awq` env var assumed), not a permissions issue as first suspected. Held-out eval (`nova_coding_eval.py`, 6 real tasks) came back ~2/6 by Claude's own informal read — same ballpark as the original 2026-07-27 result, not a clear improvement. Found a false-success report (Task 3 claimed `status: completed` after 2 turns with zero real changes, verified against the worktree directly) and a real gap in `nova_coding_eval.py` itself (diff generation misses newly-created untracked files). Marvin's call: keep `nova_remote_inference.py` pointed at the new endpoint for future re-testing, but leave `runpod_coding_agent` off — this run didn't clear Phase 3.5's "swap only once it clearly beats the baseline" bar. |
| 2026-08-03 | Pivoted from continued Qwen/Devstral training-eval rounds to building real observability (Nova Observability Initiative, `86bb7pamh`) — the coding sub-agent's 19-entry A1-G2 failure registry had been compiled entirely by hand-reading transcripts. Added a real second coding-agent backend, `nova_orchestrator_devstral.py` (native tool-calling, not the prompted `<tools>` format), so failure-registry findings could be checked against a second model family. Shipped Phase 0 (`86bb7par3`): `nova_langfuse_client.py`, real-time-verified connectivity to Langfuse Cloud (self-hosted ruled out — the Omen's 7.64GB RAM is under half Langfuse v3's own 16GB recommendation) and Phase 1 (`86bb7pawp`): `log_turn()` wired into all three turn loops (Claude/RunPod-Qwen/Devstral) plus `nova_coding_eval.py`'s eval path, gated behind `langfuse_tracing` (default off). Real per-token logprob capture required a new `nova_remote_inference.chat_with_logprobs()` — RunPod's raw `/runsync` schema silently ignores `sampling_params.logprobs`; the OpenAI-style passthrough route is what actually returns it. |
| 2026-08-04 | Observability Phase 2 (`86bb7pazm`): tagged existing guard/gate signal into Langfuse as scores keyed to the real A1-G2 registry codes (`log_guard_events()`/`log_gate_result()`, three mapping tables in `nova_langfuse_client.py`) — not new detection logic, just making already-logged signal queryable. Verified live with real guard fires from a deliberately-engineered test task. Then Phase 3 (`86bb7pb20`): the `/observability` trend dashboard — `nova_observability_dashboard.py` (failure-type frequency over time and per-model comparison from local JSONL, which has far more real history than Langfuse's own trace store since `langfuse_tracing` defaults off; uncertainty-vs-outcome from Langfuse Cloud specifically, since per-token logprobs are never persisted locally) + `nova_observability_dashboard.html` (hand-rolled inline SVG, no charting library, matching `nova_embedding_viz.html`'s existing pattern). Designed a new outcome-bucket classifier (`not_attempted`/`catastrophic`/`clean_completion`/`partial`/`unknown`) since the ticket's named buckets didn't exist anywhere in the codebase yet — verified against real branches in the actual logs before shipping. Found via live SDK introspection that the ticket's implied Langfuse read approach (`observations.get_many()`) returns no metadata at all; `trace.get()`/`trace.list()` are the real methods. Same day: cross-linked `/observability` with the Nova Controller Feed (a "Trends" button on the Feed, a "Feed" button back on the dashboard) rather than folding trend data into the Feed's strictly-chronological card design — a live-scoped tradeoff decision, not a default. |
| 2026-08-05 | **Observability Phase 4 (`86bb7pb6t`): trace-to-diff linking**, `nova_diff_link.py` + `GET /observability/diff` + a "View diff" action in the uncertainty table. Real constraint found live before designing: most historical branches no longer exist at all (`git branch --list` showed only 5 real local refs against 4765 real turns in `agent_log.jsonl` — the interactive coding-agent lane never pushes its own worktree branches, confirmed by grep), so "diff no longer available" is a first-class, expected result, not an error case. Scope decision (confirmed with Marvin): same-machine-only v1 first — a first live test of the Omen→Aero leg came back inconclusive (the Aero's own `sshd` turned out to be stopped, not a Tailscale/CreateProcess issue). **Resolved same day, retested after Marvin started `sshd` on the Aero:** the forced Omen→Aero command runs `git.exe` directly and returns real, correct output — `nova_worktree_pr.py`'s docstring claiming that class of SSH session can't run `git.exe`/`python.exe` at all overgeneralized from what it actually found for `python.exe` specifically. Branch-name validation (`nova_diff_link._validate_branch()`) found and fixed a real false-positive live: the original pattern assumed every `nova-agent/*` branch carries a timestamp suffix, wrongly rejecting a genuinely real branch — loosened to a structural check. Verified live end to end: invalid/malicious inputs rejected before any subprocess call, a real local branch's diff matched a manual `git diff` byte-for-byte, a real GitHub-pushed branch's compare URL matched `gh api`'s own result, a nonexistent branch returned a clean `unavailable` (never a 500). **Same-day cross-machine follow-up:** `nova_diff_link.py` gains `_remote_diff_from_omen()`/`_remote_diff_from_aero()`, a new `remote_diff` status. Sixth key in the Omen-Aero SSH bridge (`AERO_DIFF_KEY`, `id_ed25519_aero_diff`) + forced script `scripts/ssh_read_aero_diff.ps1` — runs `git.exe` directly rather than relaying through `nova_api.py`, so it doesn't depend on `nova_api.py` running on the Aero at all. The forced script re-validates the branch name itself rather than trusting the caller. **Started on the 4 sibling infra tickets filed alongside the Observability Initiative.** Re-ran the Omen capacity audit first (6.24GB RAM available of 7.64GB, unchanged since Langfuse went Cloud) — the same ceiling used as the filter for all 4. Real scope findings: Laminar's own lightweight quickstart bundles Postgres+ClickHouse+Quickwit (same weight class as Langfuse's self-hosted stack) — decided Laminar Cloud instead; the Gitea/Forgejo ticket (`86bb7quk1`) overlaps an older, fuller-scope ticket (`86baxuq12`) genuinely blocked on Vaultwarden — decided light stand-up only now, left the fuller scope blocked rather than faked. Shipped `86bb7qua2`: Uptime Kuma, deployed via `docker run` on the Omen (first real Docker use there, scoped to third-party tools only). Gitea/Forgejo, MLflow, and Laminar Cloud wiring scoped but not yet built. |
| 2026-08-06 | **Shipped `86bb7quga` (MLflow) and `86bb7quk1` (Forgejo, light stand-up).** Forgejo verified with a real push/clone round-trip via a fresh throwaway SSH key (content + commit hash matched exactly on a fresh clone), key and test repo removed after. Real gotchas found and fixed live on MLflow: the "bare filesystem backend" assumption doesn't hold on the current MLflow major version (deprecated/hard-refuses) — used SQLite backend store instead; default `--workers 4` used ~2GB RAM for a single-user tool, cut to `--workers 1`; `--allowed-hosts` doesn't auto-strip the port, needed the exact `host:port` form. Installing the `mlflow` Python client locally on the Aero triggered a real dependency-resolver conflict that silently uninstalled `llmcompressor` and, on a naive reinstall, cascaded into bumping `transformers`/`datasets` past what `unsloth` requires — caught via `pip check` before trusting the install, both reverted to exact original pins, verified against a real Chroma heartbeat+query afterward. Backfilled 3 real historical training runs via the low-level `MlflowClient` for accurate historical timestamps. Neither finetune script imports `mlflow` itself (the rented pod has no Tailscale route to the Omen) — each writes a local `mlflow_run_metadata.json` that rides home via the existing HF Hub checkpoint upload, and a new `nova_mlflow_ingest.py` (run from the Aero) pulls it back down and logs the real run. **Shipped `86bb7qudh` (Laminar Cloud), closing the last of the 4 sibling infra tickets.** Marvin ran `npx lmnr-cli setup` himself (account auth isn't something Claude does). New `nova_laminar_client.py` mirrors `nova_langfuse_client.py`'s calling convention but uses Laminar's own idiom (`Laminar.set_trace_session_id()` for turn-grouping, guard/gate registry codes as span input/output/metadata rather than a tags API whose parameter shape wasn't in the installed reference docs — the skill's own "don't guess APIs" rule settled that call). Real fix mid-build: `Laminar.initialize()`'s default auto-instrumentation both risked double-instrumenting the same calls Nova already logs manually and hit a real `wrap_function_wrapper()` compatibility error — disabled via `instruments=set()`, resolving both at once. Verified live end-to-end via a direct `lmnr-cli sql query` (not just "no exception raised") — real model/token/logprob/cost data landing correctly, correct registry-code mapping, all three call sites sharing one `session_id`. Flag flipped back to default-off after verification, matching every other integration's convention. **Added dependency/mutual-exclusion guards to the Controller switches panel** — real relationships found by grepping actual call sites: `coding_review_pass_enabled` requires `runpod_coding_agent`; `runpod_coding_agent`/`devstral_coding_agent`/`langgraph_orchestration` are mutually exclusive alternate backends per the file's own real `if/elif` precedence chain. New `nova_config.is_flag_toggle_allowed()` enforced both server-side (400s a blocked toggle) and client-side (grays out the button + explains why) — turning a flag OFF is always allowed, only turning ON can be blocked. Verified live: direct-call tests, real `curl` round-trips (400 blocked → 200 after satisfying the dependency), JS logic checked against the exact same payload shape via Node. **Gave the switches panel's disabled buttons a distinct dark-amber color**, verified live on the Omen after commit/push/sync. Turned on `langfuse_tracing`/`laminar_tracing` for real traffic (interactive Claude lane only). Found and fixed a real MLflow gotcha distinct from the two shipped earlier: `--cors-allowed-origins` defaults to localhost-only, separate from `--allowed-hosts` — the page loaded but every real data call 403'd, making the UI look like all 3 backfilled runs were gone when the SQLite DB was completely intact (confirmed via `MlflowClient` directly before touching the container). Fixed by adding `--cors-allowed-origins` alongside `--allowed-hosts`. Also found live, while computing a real per-model failure-pattern comparison: `GET /observability/per-model` returning an empty result — flagged for a follow-up rather than papered over. **Root-caused and fixed the `/observability/per-model` bug** the same day it was flagged — not a join-logic bug: `guard_events_log.jsonl`/`ground_truth_gate_log.jsonl` don't exist on the Omen at all, and its `agent_log.jsonl` has only 137 lines vs. the Aero's 4,765, since almost all real coding-agent activity happens on the Aero. `failure_frequency_over_time()` had the identical bug, unreported but confirmed present. Fixed with the same cross-machine pattern `/qwen-swap-status`/`/worktree-status`/`/training-data-status` already established: new `nova_observability_status.py`, a 7th Omen-Aero SSH key (`observabilitylogs`) and forced script `ssh_read_observability_logs.ps1`. Verified live end-to-end: a real SSH round-trip returned 321KB of real bundled log data, `per_model_comparison()` called directly on the Aero returned real per-model gate-pass-rates in the same ballpark as an earlier manual pooled-by-backend estimate, `view: "combined"` confirmed from both machines. **Ran `nova_coding_corrector.py` for real** against the 35 real pending review entries — 31 succeeded, 4 failed transiently and stay pending, taking real usable DPO pairs from 5 to 36. Spot-checked 2 real corrections against their original flawed diffs — both genuinely good, surgical fixes. But found the 36 pairs are only 6 distinct underlying tasks repeated 4-9 times each, root-caused to `coding_review_log.jsonl` being populated almost entirely by `nova_coding_eval.py`'s own unconditional per-run seeding (bypassing the `runpod_coding_agent`+`coding_review_pass` flag gate entirely), and that harness has a hardcoded `EXPECTED_HELD_OUT_COUNT = 6` — so this round's "36 pairs" is really the eval's own fixed held-out suite reviewed repeatedly, not organic production diversity. Real implication: training DPO on these pairs then evaluating against the same 6-task suite would be train/test leakage — decided to hold the 36 pairs as reference data, not train on them yet. Separately corrected an analytical mistake: `goal_reanchor`'s guard-fire rate is a fixed-cadence reminder with no relationship to real drift — the actual severe-scope-violation check (`D1`) fires zero times in real data; the real dominant failure is incompleteness, which led directly to the `self_verify_nudge` fix. |
| 2026-08-11 | Installed a real NVIDIA driver on the Omen's GTX 1050 Ti Mobile, turning it from present-but-unusable (per the 2026-07-21 Omen Capacity Audit) into real usable GPU compute for the first time. `ubuntu-drivers devices` recommended `nvidia-driver-580` for this card. **Real blocker found before installing anything:** the Omen boots UEFI with Secure Boot enabled, and installing a proprietary kernel module under Secure Boot normally triggers MOK (Machine Owner Key) enrollment — a blue console screen that appears on the *next* reboot and needs physical keyboard input to confirm, which is impossible to satisfy over a non-interactive SSH session. Also confirmed live that `sudo` on the Omen requires a real password even from this session's own SSH connection (`sudo -n` failed outright) — matches the existing house rule that sudo on the Omen always needs Marvin directly, not automation. Decision: disable Secure Boot first via BIOS rather than enroll a MOK key, since the ticket's real long-term win is skipping re-enrollment on every future kernel/DKMS rebuild, not saving this one physical-console trip (both routes need it once). Checked for a TPM-sealed-disk-encryption risk before recommending this (a real way disabling Secure Boot can silently lock someone out of an encrypted disk) — `/etc/crypttab` is empty, no LUKS volumes in use, so that risk didn't apply here.

**Getting into BIOS turned out to be its own real obstacle course.** The documented Esc/F10-at-splash-screen approach never worked — the HP splash screen wasn't reliably appearing at all, most likely fast SSD boot skipping past it before it could render. Fixed with `sudo systemctl reboot --firmware-setup` (systemd 255, confirmed UEFI via `/sys/firmware/efi`) to request firmware setup directly from the running OS, sidestepping the splash-timing problem entirely. **Real gotcha on this specific HP unit:** that command doesn't land in BIOS Setup directly — it lands on HP's intermediate Startup Menu (the F1/F2/F7/F9/F10/F11 screen, same one `Esc` normally reaches), and `F10` from there is what actually reaches Computer Setup with Secure Boot Configuration under the Security tab.

**Real incident, found live mid-process: Marvin's local Ubuntu account password was forgotten.** Recovered via GRUB recovery mode rather than guessing or reinstalling: (1) reboot and hold `Shift` immediately after POST to force the GRUB menu to actually display, since Ubuntu's default quiet/fast-boot config hides it otherwise — the same splash-timing root cause as the BIOS problem above, just one layer later in the boot chain; (2) a first attempt landed at a bare `grub>` command-line prompt instead of the menu (GRUB loaded fine, just didn't render menu entries) — fixed live with `normal`, which reloads the standard menu-rendering path; (3) `normal` alone then auto-booted straight past the menu into the login screen without waiting, because `GRUB_TIMEOUT` is effectively `0` on quiet-boot configs — fixed with `set timeout=-1` before `normal`, which holds the menu indefinitely for a real selection, and doesn't persist past that one boot since it's not written back to `grub.cfg`; (4) two kernels were present in "Advanced options for Ubuntu" (`6.8.0-134-generic`, the one `uname -r` had reported earlier this session, and a newer already-installed-but-not-yet-booted `6.8.0-137-generic`) — expected Ubuntu fallback-kernel behavior from a routine `apt upgrade`, not a sign of anything broken; either kernel's recovery entry works equally for this, the newer one was used; (5) selected **"root – Drop to root shell prompt"** from the recovery menu (no password needed by default), `mount -o remount,rw /` to make the read-only recovery mount writable, then `passwd marvinroyal5` to set a new password. That new password resolved both the console-login gate and the `sudo` password needed for the rest of this task, since Ubuntu uses the same account password for both by default.

**Verified live end-to-end after both reboots** (BIOS-disable reboot, then the post-driver-install reboot): `mokutil --sb-state` reports `SecureBoot disabled`; `nvidia-smi` reports the GTX 1050 Ti Mobile correctly — driver `580.173.02`, CUDA 13.0, 4096MiB VRAM, idle at 3MiB used; `systemctl is-active nova-api nova-chroma` returned `active`/`active` after each reboot, confirming no lasting outage to either service despite both being genuinely live and running (`nova-api`/`nova-chroma` were confirmed `active` immediately before the first reboot too, so this wasn't a coincidental already-down state). Updated CLAUDE.md's HP Omen Headless Server and Omen Capacity Audit sections to reflect the GPU is no longer driverless. |
| 2026-08-29 | **Eval Harness Initiative 2 (`86bbcfv9d`) — audited `nova_aci_harness.py`'s hybrid-verify gate and split its generative half.** The gate is two stages: `_run_real_tests()` (execution, cheap, deterministic, the same check final scoring uses) then, only once every real test passes, one `claude-sonnet-5` style call (`_generative_style_verifier`, `max_tokens=200`, no `tools` — Console-billed, not subscription). The style call was checking two unrelated things — a *gamed/hardcoded* solution (output values copied from the visible test cases, a real cheating issue) and *unidiomatic* Python (subjective, and the solution already passes every objective test) — and collapsing both into one `CONCERNS` verdict that hard-blocked `done`. Blocking on the idiom half is the confirmed trigger for the `octal` loss-of-working-solution failure (memory `project_early_abandon_ab_and_snapshot_finding`): a passing `return int(digits, 8)` flagged unidiomatic, the model rewrote it as a self-shadowing nested `def` chasing the nudge, broke it, never recovered. `--regression-guard` only catches that after the fact (~2.4% of runs) by discarding the style feedback entirely. **Fix:** `_generative_style_verifier` now returns `ACCEPT` / `GAMED: <reason>` / `IDIOM: <reason>` (unrecognized → `ACCEPT`, fail open). `GAMED` blocks `done` unconditionally in both flag states, with a nudge that names the gaming concern specifically. `IDIOM` behaviour depends on a new opt-in `--advisory-idiom` flag (default off): off → blocks exactly as `CONCERNS` did (clean A/B baseline preserved); on → logged as `style_idiom_note` on the result and accepted, no nudge. The 3-way categorization runs on every hybrid-verify pass regardless of the flag, so every run logs which concern fired (new result fields `advisory_idiom_enabled`/`style_idiom_note`/`style_gamed_rejections`; batch + single-run output print the split). No interaction with `--regression-guard` by construction — an accepted `IDIOM` run ends `completed` with tests passing, so the run-end snapshot restore never triggers. **Verified 2026-08-29** via direct `_hybrid_verify_gate` calls against a real `two-fer` working copy: idiomatic solution → `ACCEPT` in both modes; egregiously unidiomatic (char-by-char `while`-loop string building) → `IDIOM`, `gate_passed=False` with the flag off / `True` with it on; hardcoded `if name == 'Alice'` ladder → `GAMED`, `gate_passed=False` in both modes. Plus a full harness run (`two-fer --hybrid-verify --advisory-idiom`, real Ollama + one real style call) completing clean with the new tuple unpacking and log fields. Eval-harness only — `_hybrid_verify_gate`/`_generative_style_verifier` have zero callers outside `nova_aci_harness.py`, no production path touched. New `docs/aci-hybrid-verify-gate-audit.md` (full audit, including an "audited, not changed" list — execution-first ordering, the separate `test_fail_nudges`/`style_concern_nudges` budgets from the 2026-08-20 fix, and the fail-open default all stay) and `scripts/run_advisory_idiom_ab_test.py` (written, **not yet run** — ~$1–2 Console/batch; A/B deferred to a separate go-ahead per Marvin). Flag stays opt-in; promote to default-on (with `--no-advisory-idiom`) only if a real full-corpus A/B shows a pass-rate edge *without* raising `GAMED` rejections. Open question flagged for Phase 5 / Initiative 3 (`86bbcfva4`): whether `GAMED` detection is itself reliable on tiny exercises, where a genuinely simple correct solution can look "too simple". |
| 2026-08-29 | **Initiative 2 continued — built the individual-ablation infra for `nova_aci_harness.py`'s four always-on turn-loop guards.** `docs/aci-failure-mechanism-analysis.md` measured `repeat_failed_call` / `done_without_edit` / `same_path_repeated_failure` cumulatively (baseline → 2-guard → 3-guard as a block) and never isolated any one guard's contribution — the wrong granularity for "audit existing gates individually" ("some gates carry most of the lift, others actively hurt precision" — you can't see that from a cumulative curve). New `ABLATABLE_GUARDS` frozenset (the four: `repeat_failed_call`, `done_without_edit`, `same_path_repeated_failure`, `multiple_calls_ignored` — `--hybrid-verify`/`--early-abandon`/`--regression-guard` are separate flagged axes, not part of this cluster). `run_exercise()`/`run_all_exercises()` gained `disabled_guards: frozenset[str]`, and there's a repeatable, `choices`-validated `--disable-guard NAME` CLI flag. When a guard is in the set its **effect** is skipped and the loop reverts to pre-guard behaviour at that exact point (re-execute the byte-identical repeat; accept the no-edit `done` and stop, deliberately NOT falling through to the hybrid-verify gate which assumes a real edit exists; drop the same-path corrective note; drop the multi-call nudge — the latter refactored into a small `_multi_call_suffix()` closure shared by its 3 call sites), but a would-have-fired count is still recorded per run in a new `guards_suppressed` result field, so a flat ablation result reads correctly ("guard doesn't matter" vs. "guard barely fired in this corpus"). New `scripts/run_guard_ablation.py`: baseline (all guards on) + one condition per guard (that guard off), full 31-exercise corpus, `repeat=N`, `--hybrid-verify` left off so the whole batch spends **$0** (Ollama only); reports pass rate, avg turns, `max_turns_reached` %, would-have-fired counts, and per-guard deltas vs. baseline. New `docs/aci-guard-cluster-ablation.md`. **Verified via a deterministic scripted test** (fake Ollama client returning a fixed turn sequence, no API, no real model): with `repeat_failed_call` active a thrice-repeated byte-identical broken edit fires the guard twice and is never re-executed; disabled, it fires zero times, `guards_suppressed["repeat_failed_call"] == 2`, and the call re-executes each time. With `done_without_edit` active an immediate `done` ends `abandoned_after_nudge` after 2 nudges; disabled, it ends `completed` on turn 1 with `guards_suppressed["done_without_edit"] == 1`. `_print_summary()` and the single-run print both surface the disabled/would-have-fired data. The ~310-run ablation batch (5 conditions × 31 × repeat 2, a few hours) is deferred to a separate go-ahead. `86bbcfv9d` moved `to do` → `in progress`. Prediction going in: pass rate flat across all conditions (these guards don't lift the capability ceiling — established repeatedly); real signal in `avg_turns`/`max_turns_reached` %, where `same_path_repeated_failure` most likely carries the efficiency lift the 3-guard cumulative run showed. |
