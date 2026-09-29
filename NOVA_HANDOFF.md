# Nova Handoff

Read this first, on either side, before starting work. Rewrite the Current State
section in full when you close out a session — this is not a narration log, it's
current truth. The Recent Log below is a bounded history, not the source of truth.

Lives at `C:/Nova/NOVA_HANDOFF.md`, alongside `NOVA_STATUS.md` (board digest) and
`NOVA_BUILD_LOG.md` (narrative history) — same root-level-doc convention, different job:
those two are generated/append-focused, this one is a small, rewritten-in-place
sync point between a Chat planning session and a Code implementation session.

## Current State
*(last updated: Code · 2026-09-29)*

**Active thread:** Two small local-model coding-agent experiments, inspired by
a multi-agent-orchestration video (OpenRig) but grounded against what Nova
already has. **Experiment 1 is DECIDED — Experiment 2 is next up and can
start whenever.**

**Experiment 1 — DECIDED (2026-09-28, Marvin): keep `goal_reanchor`
default-ON.** `GUARD_GOAL_REANCHOR` (ported from `nova_orchestrator_runpod.py`
into `nova_aci_harness.py`) is built, committed, pushed. Cumulative ablation
reached the real n≈240/condition trust bar (batch 1 n=60 + confirmatory batch
n=180, both $0):

| | Pass | Avg turns | max_turns% |
|---|---|---|---|
| baseline | 18/240 (7.5%) | 8.83 | 32.9% |
| -goal_reanchor | 11/240 (4.6%) | 8.66 | 31.7% |

Pass rate favored keeping the guard consistently across both batches, but
avg_turns/max_turns% reversed sign between them (batch 1 said the guard
helped efficiency, the confirmatory batch said the opposite) — the same shape
of result that got `same_path_repeated_failure`/`_format_list_result()`
demoted to opt-in previously. Marvin's call this time was different: keep it
default-ON anyway, since pass rate favored it consistently both times and
it's a cheap intervention (a periodic text nudge, not a real cost center) —
the reversed efficiency signal is plausibly noise rather than a real
negative effect. No code change needed (already registered default-ON).
Full numbers/reasoning in `project_goal_reanchor_ablation_result_1.md`.

**Real live incident during the confirmatory run, resolved:** it appeared to
die around run 100/180 (no python process, free RAM had dropped 13.6GB→6GB) —
same low-RAM-during-a-long-run pattern flagged below from 2026-09-26. An
explicit `ollama stop` (the model was stuck loaded well past its claimed
keep-alive countdown, not actually unloading on its own) recovered ~8GB and
the run resumed on its own. A redundant duplicate chunk was accidentally
launched on top of it before that recovery was noticed — Marvin killed it by
hand once diagnosed, no lasting effect on the numbers above. Real lesson:
**`ollama ps`'s "until" countdown cannot be trusted as proof a model has
actually unloaded — verify via free RAM or `ollama stop` + recheck, not just
the countdown display.**

**Still just scoped (not started) — Experiment 2, planner/executor split-brain:**
Test separating "which atomic action next" from "produce this action's exact
params" into two role-scoped Ollama calls inside `nova_aci_harness.py`'s turn
loop (~1140-1260), same model in both roles first (`qwen2.5-coder:7b` x2) to
isolate role-narrowing from model diversity. Add a `handoff_mismatch` counter
alongside the existing `guard_fires` dict so a negative result is diagnosable,
not just anecdotal. Eval on corpus slugs with a real nonzero baseline pass
rate (`raindrops`, `scrabble-score`, `two-fer`, `binary`, `ledger`), per
`project_squad_pilot_result_1.md`'s own recommendation — avoids the floor
effect that made the squad pilot's first run inconclusive.

**Also scoped, not started — Experiment 3, pinned context in `collapse_history()`
(2026-09-27):** `nova_coding_aci.collapse_history()` is recency-only (last
`HISTORY_KEEP_RECENT = 8` messages verbatim, everything older replaced by one
placeholder the model can't expand). Test keeping the last 8 plus pinned items —
latest successful `view` of each file being edited, last rejected edit + its
error — on the same slugs/ablation infra as #1. Inspired by AnyMAC's
Next-Context Selection. Open check first: can `GUARD_GOAL_REANCHOR`'s note get
collapsed away a few turns after injection? Detail in
`project_aci_context_selection_experiment_3.md`.

**Sequencing decision:** #1 is decided, so #2 (planner/executor split-brain)
can start next session with no confound risk from #1 still being open. #3
(pinned context) still goes after #2 for the same reason as before.

**Unrelated but real infra fix this session (2026-09-28):** the coding-track's
daily synthetic-data cron (`run_synthetic_task_gen_scheduled.ps1`) had been
silently failing for 23 days straight (every run since 2026-09-06) —
`data/coding_training/synthetic/synthetic_task_pairs.jsonl` sat stalled at 221
rows despite the cron firing on schedule. Root cause: the `anthropic` SDK
(0.109.2) prints a harmless stderr notice on client construction whenever an
explicit API key is present, and PowerShell 5.1 promotes any redirected native
command's stderr line to a terminating error under
`$ErrorActionPreference = "Stop"` — killing the whole wrapper before any
commit got processed. Fixed in both `run_synthetic_task_gen_scheduled.ps1` and
`run_corrector_scheduled.ps1` (same latent pattern, hadn't tripped yet there),
committed, pushed, Omen synced. Verified live — a manual re-run completed
cleanly, 231 rows now. Full detail: `feedback_powershell_native_stderr_abort.md`.

**Three more watch-item memories saved this session (2026-09-28-29), all from
Marvin's ongoing lecture-notes questions, none actionable yet:**
Dynamic Mixed-Precision Routing (per-step precision router — no matched
quantized/full-precision model pair exists in Nova to route between) and
SafeDream (multi-turn jailbreak detection — no adversarial-user threat model
in Nova, but its CUSUM cumulative-evidence idea is a plausible smarter
successor to `GUARD_GOAL_REANCHOR`'s fixed-interval design, worth revisiting
once Experiment 1 settles). Full list in `MEMORY.md`.

**Relevant prior art surfaced this session (both already in project memory,
just newly connected to this thread):** `nova_squad_pilot.py`
(`project_squad_pilot_result_1.md`) is the closest existing cousin to the
"fleet of small models" idea — not a role split, but retrieval-augmented
single-model context — and leaned negative on turn-efficiency (4.5x more
guard fires under retrieval). Direct warning for #2: injecting a second
model's output into a small model's context can act as noise, not help.

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
  and the earlier Handoff/Tutor-docs work (both had been sitting uncommitted).
  Found+fixed a real 23-day-silent cron failure (PowerShell native-stderr-abort
  bug, both scheduled wrappers). Researched two more lecture papers (Dynamic
  Mixed-Precision Routing, SafeDream) plus quick concept questions (KL
  divergence, GRPO, sparse attention, ImageMagick, a dedicated test-manager
  agent idea, multimodal training, Computer Use feasibility on Windows) — all
  saved/answered, none actionable now. Confirmatory ablation batch survived a
  RAM-pressure stall (Ollama stuck loaded past its keep-alive countdown,
  `ollama stop` recovered it) and a self-inflicted redundant duplicate chunk
  (killed by Marvin once diagnosed), then completed cleanly at n=240
  cumulative. **Experiment 1 decided: keep `goal_reanchor` default-ON** (see
  Current State above) — Experiment 2 is unblocked for next session.
