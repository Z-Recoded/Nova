# Nova Handoff

Read this first, on either side, before starting work. Rewrite the Current State
section in full when you close out a session — this is not a narration log, it's
current truth. The Recent Log below is a bounded history, not the source of truth.

Lives at `C:/Nova/NOVA_HANDOFF.md`, alongside `NOVA_STATUS.md` (board digest) and
`NOVA_BUILD_LOG.md` (narrative history) — same root-level-doc convention, different job:
those two are generated/append-focused, this one is a small, rewritten-in-place
sync point between a Chat planning session and a Code implementation session.

## Current State
*(last updated: Code · 2026-10-04)*

**Paused here (2026-10-04) — golden-benchmark blend-rate check, essentially closed.**
Marvin asked whether `nova_benchmark.py --golden` costs Anthropic spend (funds are
low). Answer from the code: no — it only uses local Ollama (`llama3.2`) + Chroma, no
Claude calls; the one unchecked edge is whether any golden query carries
`CODING_AGENT_PREFIX` (none of the 8 query texts look like one). Ran it 4 times
today: all passed, 0 routing mismatches, **fiction blend rate 0.0% every time**,
avg latency 1781-3198ms (history median 3198ms, so normal). The earlier 0.333 blend
rate (07-27 to 08-08 runs) was always the single query "tell me a story" (no named
character). Neither `6b732d2` nor the uncommitted changes can affect that query;
most likely cause of the fix is `928e4d9` (`detect_blending()` false-positive fix,
2026-08-08). **One loose end:** a run at 2026-08-08 20:20 still showed 0.333 eight
hours *after* that commit, unexplained, and no runs exist between 08-08 and 10-03.
4 clean runs today make a nondeterministic return unlikely but not impossible.

**Committed + pushed + Omen-synced at Marvin's go-ahead (2026-10-04, `e418ca6`):**
the experimental `multi_character_retrieval` flag (default off) in
`nova_config.json`, `nova_config.py`, `nova_query.py` —
`_find_all_named_character_files()`, `_retrieve_per_named_character()`,
`MULTI_CHARACTER_CHUNKS_PER_FILE = 2`, gated in `ask()`. Pushed to `forgejo` then
`origin`. **Never reviewed with Marvin and never tested with the flag on** — it was
committed because he said to push, not because it was validated.

**Omen sync race (not fixed):** `nova_omen_sync.py` reported "FAILED at verify" after
this push — `nova-api` started while `nova-chroma` was still restarting, crashed at
import (`graph_builder.py` can't reach Chroma), and systemd auto-restarted it. It
was listening on :8001 within ~2 minutes and `/headroom` returned a real payload.
The sync script treats this recoverable race as a failure; consider a retry/wait in
its verify step.

**Decision-model (Jev-style) evaluation, 2026-10-04 — closed as negative/inconclusive,
no active work.** Marvin pasted two videos about TypeSafe's Jev (single-pass
Boolean/Choice/Score model with confidence). Found it is REAL (Vercel changelog
2026-09-16, `typesafe-ai/jev`; a stale memory had called it satire — corrected in
`project_logprob_classifier_system1_watch.md`). Hosted price ~$0.042/M input tokens
(secondary sources, unverified); not a cost problem, a privacy/dependency one. Ran a
free local test: Fastino's open GLiNER2.5-Decide vs. the Claude blend judge on the 54
`training_flags.jsonl` rows + 54 easy controls. Best variant (yes/no with label
descriptions) flagged 76% of blends but also 26% of controls; confidence did not
separate hits from misses; a confident "no" can't safely skip the Claude judge. Fails
the 90% recall bar — NOT a replacement. Scratch venv/weights deleted, nothing added to
the repo, no Anthropic spend. A fairer test would need fine-tuning on the 1,500 judge
verdicts + regenerated answers + hard negatives — not started, no clear payoff.

**Next pick-up (Marvin's call):** (a) review/test the `multi_character_retrieval`
path with the flag on, (b) make `nova_omen_sync.py`'s verify tolerate the restart
race, or (c) start Experiment 2 below.

**Experiment 1 — DECIDED (2026-09-28, Marvin): keep `goal_reanchor` default-ON.**
Cumulative ablation n=240/condition: baseline 18/240 (7.5%) vs -goal_reanchor
11/240 (4.6%); efficiency signal reversed between batches, Marvin kept it ON anyway
since pass rate favored it both times. Detail: `project_goal_reanchor_ablation_result_1.md`.
Lesson kept: `ollama ps`'s "until" countdown can't be trusted — verify unload via free
RAM or `ollama stop`.

**Still just scoped (not started) — Experiment 2, planner/executor split-brain:**
Separate "which atomic action next" from "produce this action's exact params" into
two role-scoped Ollama calls inside `nova_aci_harness.py`'s turn loop (~1140-1260),
same model both roles first (`qwen2.5-coder:7b` x2). Add a `handoff_mismatch` counter
beside `guard_fires`. Eval on slugs with a real nonzero baseline (`raindrops`,
`scrabble-score`, `two-fer`, `binary`, `ledger`). Warning from
`project_squad_pilot_result_1.md`: injecting a second model's output into a small
model's context can act as noise.

**Also scoped, not started — Experiment 3, pinned context in `collapse_history()`:**
`nova_coding_aci.collapse_history()` is recency-only (`HISTORY_KEEP_RECENT = 8`).
Test last 8 plus pinned items (latest successful `view` per edited file, last
rejected edit + error). Open check first: can `GUARD_GOAL_REANCHOR`'s note get
collapsed away? Detail: `project_aci_context_selection_experiment_3.md`. Goes after #2.

**Reminder, 2026-10-03:** the Omen's nova-infra downtime window (~2:45AM-12:05PM ET)
is live; anything needing Chroma/`nova_query` fails inside it. Today's runs were
after 12:05PM ET.

## Open questions for the other side
Numbered so they can be answered and removed, not left to linger in prose.

1. The Nova Tutor / Overworld ClickUp chain has pending SMT-naming comments that
   never got posted (rate limit) — see `nova_tutor_smt_naming_updates.md` if that
   file was ever handed to you separately. Worth folding into this handoff's log
   once posted, rather than tracking it in two places.

## Known drift
Design doc says X, implementation does Y (or vice versa) — stays here until
reconciled, either by updating the doc or fixing the code. Empty when clean.

- CLAUDE.md notes (Nova Tutor section) that the three newer "Overworld" ClickUp
  tasks (`86bbvcbby`/`86bbvcbcp`/`86bbvcbcv`) aren't yet reconciled in writing
  against the original 7-phase Tutor design doc on Drive. Not this file's job to
  fix, but flagging it here since it's a live cross-reference gap.

## Do-not-repeat
Mistakes already made and corrected, so neither side re-does them.

- Search the Nova Board before creating any task — 5 duplicate tutor tasks were
  created once before this rule existed.

---
## Recent log
Capped at ~10 entries. When it fills, summarize the oldest few into one line
and delete them — don't let this grow unbounded.

- **2026-09-20 · Chat:** Drafted this handoff file structure, confirmed `C:\Nova`
  as the real repo root (connected the folder mid-session), and placed the file
  at `C:\Nova\NOVA_HANDOFF.md`.
- **2026-09-20 · Chat:** Wired `NOVA_HANDOFF.md` into CLAUDE.md (file tree,
  Section 11 startup checklist read step, Section 11 end-of-session rewrite
  reminder) so it's part of the documented routine, not a side file.
- **2026-09-26 · Chat:** Scoped two ACI-harness experiments (goal-reanchor
  port, planner/executor split-brain) after a video-inspired discussion on
  multi-agent coordination failure modes; found both have close existing
  prior art (`nova_orchestrator_runpod.py`'s goal_reanchor/self_verify_nudge,
  `nova_squad_pilot.py`'s retrieval pilot) that reshaped the scope before
  anything was built. Session paused here — no code changes yet.
- **2026-09-26 · Chat (same session, resumed):** Built Experiment 1 (ported
  `GUARD_GOAL_REANCHOR` into `nova_aci_harness.py`, wired into
  `ABLATABLE_GUARDS`), smoke-tested both guard states on `bob`, kicked off
  the real ablation batch in the background. Awaiting result before deciding
  keep/revert and before starting Experiment 2.
- **2026-09-27 · Code:** Marvin checked in; no code changes. Researched two
  lecture-sourced papers (MAPLE, AnyMAC) — both saved as watch-item memories,
  neither actionable now. Read `collapse_history()`, scoped Experiment 3
  (pinned context) above. Experiment 1's confirmatory batch still pending
  RAM headroom.
- **2026-09-28 · Code:** Researched a third lecture-sourced paper (LTS,
  "Learning to Share") — saved as a watch-item memory, not actionable (needs
  parallel agent teams Nova lacks). Session closed; still no code changes.
- **2026-09-28/29 · Code (new session):** Committed+pushed+synced Experiment 1
  and the earlier Handoff/Tutor-docs work. Found+fixed a real 23-day-silent cron
  failure (PowerShell native-stderr-abort bug, both scheduled wrappers).
  Researched Dynamic Mixed-Precision Routing and SafeDream plus several concept
  questions — saved/answered, none actionable. Confirmatory ablation survived a
  RAM-pressure stall and completed at n=240 cumulative. **Experiment 1 decided:
  keep `goal_reanchor` default-ON** — Experiment 2 unblocked.
- **2026-10-04 · Code:** No code changes. Confirmed golden benchmark makes no
  Anthropic calls (local Ollama only); ran it 4x, all clean, blend rate 0.0%
  each time. Traced old 0.333 blend rate to the generic "tell me a story" query
  (likely fixed by `928e4d9`; one post-fix 08-08 run unexplained). Uncommitted
  `multi_character_retrieval` work committed+pushed (`e418ca6`) on Marvin's
  go-ahead, untested with the flag on; Omen synced (verify falsely failed on a
  nova-api/Chroma restart race, recovered on its own). Then evaluated Jev-style
  decision models: confirmed Jev is real (corrected a stale "satire" memory), ran a
  free local GLiNER2.5-Decide test vs. the Claude blend judge — negative/inconclusive,
  scratch files deleted, memory updated. Paused for the day.
